from uuid import uuid4

from src.services.file_context import load_run_attachment_ids


def test_load_run_attachment_ids() -> None:
    first = uuid4()
    second = uuid4()
    payload = f'{{"attachment_ids":["{first}","{second}"],"triggered_by":"chat"}}'
    assert load_run_attachment_ids(payload) == [first, second]
    assert load_run_attachment_ids(None) == []
    assert load_run_attachment_ids("{not json") == []
