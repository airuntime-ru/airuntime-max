import time
from datetime import UTC, datetime, timedelta

from src.services import image_janitor


class _FakeImage:
    def __init__(self, image_id: str, tags: list[str], created) -> None:
        self.id = image_id
        self.tags = tags
        self.attrs = {"Created": created}


class _FakeImageRef:
    def __init__(self, image_id: str, tags: list[str] | None = None) -> None:
        self.id = image_id
        self.tags = tags or []


class _FakeContainer:
    def __init__(
        self,
        image_id: str,
        *,
        tags: list[str] | None = None,
        name: str = "container",
        labels: dict[str, str] | None = None,
        status: str = "running",
        created=None,
    ) -> None:
        self.image = _FakeImageRef(image_id, tags)
        self.name = name
        self.labels = labels or {}
        self.status = status
        self.attrs = {
            "Created": created or _iso(10),
            "Config": {"Image": (tags or [""])[0]},
        }
        self.removed = False
        self.stopped = False

    def stop(self, timeout: int = 10) -> None:
        self.stopped = True
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeImages:
    def __init__(self, images: list[_FakeImage]) -> None:
        self._images = images
        self.removed: list[str] = []

    def list(self) -> list[_FakeImage]:
        return list(self._images)

    def remove(self, image: str, force: bool = False) -> None:
        self.removed.append(image)


class _FakeContainers:
    def __init__(self, containers: list[_FakeContainer]) -> None:
        self._containers = containers

    def list(self, all: bool = False) -> list[_FakeContainer]:
        return list(self._containers)


class _FakeClient:
    def __init__(self, images: list[_FakeImage], containers: list[_FakeContainer]) -> None:
        self.images = _FakeImages(images)
        self.containers = _FakeContainers(containers)


def _iso(hours_ago: float, *, nanos: bool = False) -> str:
    dt = datetime.now(UTC) - timedelta(hours=hours_ago)
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if nanos:
        # Docker's real precision (9 fractional digits) - exercises the truncate-to-6 path.
        return f"{base}.{dt.microsecond:06d}789Z"
    return f"{base}Z"


def test_sweep_removes_only_owned_scratch_images():
    """Unknown host images are never inferred to belong to AIRuntime."""
    images = [
        _FakeImage("img-dangling", [], _iso(10)),
        _FakeImage("img-platform", ["airuntime-generated-site-abc123456789:latest"], _iso(10)),
        _FakeImage("img-referenced", ["postgres:16-alpine"], _iso(10)),
        _FakeImage("img-unrelated", ["customer-app:latest"], _iso(10)),
        _FakeImage("img-fresh", ["airuntime-scratch-fresh:latest"], _iso(0.1)),
        _FakeImage("img-orphan", ["airuntime-scratch-project-a:latest"], _iso(10)),
        _FakeImage("img-orphan-ns", ["airuntime-scratch-project-b:latest"], _iso(10, nanos=True)),
    ]
    containers = [_FakeContainer("img-referenced")]
    client = _FakeClient(images, containers)

    removed = image_janitor.sweep_unrecognized_images(client)

    assert set(removed) == {
        "airuntime-scratch-project-a:latest",
        "airuntime-scratch-project-b:latest",
    }
    assert set(client.images.removed) == {"img-orphan", "img-orphan-ns"}


def test_sweep_handles_epoch_created_and_leaves_unparseable_created_alone():
    old_epoch = time.time() - 10 * 3600
    images = [
        _FakeImage("img-epoch-old", ["airuntime-scratch-epoch:latest"], old_epoch),
        _FakeImage("img-bad-created", ["airuntime-scratch-bad:latest"], "not-a-date"),
    ]
    client = _FakeClient(images, [])

    removed = image_janitor.sweep_unrecognized_images(client)

    assert removed == ["airuntime-scratch-epoch:latest"]


def test_sweep_never_touches_a_running_container_error_path():
    """images.remove() raising (e.g. a container started using it between listing and removal)
    must not blow up the sweep or count as removed."""

    class _RaisingImages(_FakeImages):
        def remove(self, image: str, force: bool = False) -> None:
            raise RuntimeError("image is being used by a running container")

    client = _FakeClient(
        [_FakeImage("img-orphan", ["airuntime-scratch-project:latest"], _iso(10))], []
    )
    client.images = _RaisingImages(client.images._images)

    removed = image_janitor.sweep_unrecognized_images(client)

    assert removed == []


def test_container_sweep_removes_old_codex_and_leaked_scratch_but_keeps_canonical_app():
    project_id = "12345678-1234-4234-8234-123456789012"
    codex = _FakeContainer(
        "img-codex",
        name="airuntime-codex-old",
        labels={"airuntime.role": "codex"},
        status="exited",
    )
    leaked = _FakeContainer(
        "img-scratch",
        tags=["airuntime-scratch-12345678:latest"],
        name="youthful_euclid",
        labels={"airuntime.managed": "true", "airuntime.project_id": project_id},
    )
    canonical = _FakeContainer(
        "img-scratch",
        tags=["airuntime-scratch-12345678:latest"],
        name="airuntime-12345678",
        labels={"airuntime.managed": "true", "airuntime.project_id": project_id},
    )
    fresh_preview = _FakeContainer(
        "img-preview",
        name="preview-fresh",
        labels={"airuntime.role": "preview"},
        created=_iso(0.1),
    )
    client = _FakeClient([], [codex, leaked, canonical, fresh_preview])

    removed = image_janitor.sweep_temporary_containers(client)

    assert set(removed) == {"airuntime-codex-old", "youthful_euclid"}
    assert codex.removed is True
    assert leaked.stopped is True and leaked.removed is True
    assert canonical.removed is False
    assert fresh_preview.removed is False


def test_container_sweep_does_not_inspect_a_pruned_image():
    """A stopped container can outlive its image; Docker then raises from container.image."""

    project_id = "87654321-1234-4234-8234-123456789012"
    container = _FakeContainer(
        "img-pruned",
        tags=["airuntime-scratch-87654321:latest"],
        name="old_scratch_run",
        labels={"airuntime.managed": "true", "airuntime.project_id": project_id},
        status="exited",
    )

    class _PrunedImageContainer:
        name = container.name
        labels = container.labels
        status = container.status
        attrs = container.attrs
        removed = False

        @property
        def image(self):
            raise RuntimeError("No such image")

        def remove(self, force: bool = False) -> None:
            self.removed = True

    pruned = _PrunedImageContainer()
    client = _FakeClient([], [pruned])

    removed = image_janitor.sweep_temporary_containers(client)

    assert removed == ["old_scratch_run"]
    assert pruned.removed is True
