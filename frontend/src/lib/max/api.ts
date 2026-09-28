/**
 * Mini app API client.
 *
 * Authentication is the MAX launch string and nothing else: no JWT, no cookie, no user id
 * in a body. Every call carries `X-Max-Init-Data`, the backend verifies its signature
 * against the bot token, and whatever user it resolves to is the user the request is for.
 */

import { getInitData } from "@/lib/max/bridge";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type ServiceItem = {
  title: string;
  description: string;
  price_rub: number | null;
  duration_min: number | null;
  category: string;
  image_url: string;
  badge: string;
};

export type LeadStatus = "new" | "confirmed" | "declined" | "done";

export type CustomerLead = {
  id: string;
  item_title: string;
  slot_label: string;
  status: LeadStatus;
  created_at: string | null;
  scheduled_at: string | null;
};

export type ServiceConfig = {
  kind: "booking" | "menu" | "landing";
  title: string;
  tagline: string;
  about: string;
  accent: string;
  mood: "calm" | "warm" | "bold" | "minimal";
  layout: "classic" | "editorial" | "cards" | "poster";
  color_scheme: "light" | "dark";
  heading_style: "sans" | "serif" | "display";
  hero_image: string;
  design_concept: string;
  nav_style: "tabs" | "pills" | "rail" | "none";
  hero_style: "split" | "fullbleed" | "editorial" | "typographic" | "collage";
  card_style: "image-top" | "horizontal" | "overlay" | "minimal" | "numbered" | "menu";
  radius_style: "sharp" | "soft" | "round";
  density: "airy" | "balanced" | "compact";
  section_order: Array<"hero" | "story" | "catalog">;
  heading_font?: string;
  palette?: { bg: string; surface: string; ink: string; accent2: string };
  pattern?: "none" | "grain" | "dots" | "grid" | "rings" | "stripes" | "glow";
  hero_tone?: "" | "accent" | "tint" | "plain" | "ink";
  kicker?: string;
  catalog_title?: string;
  highlights?: Array<{ value: string; label: string }>;
  marquee?: boolean;
  contacts: { phone: string; address: string; hours: string };
  items: ServiceItem[];
  slots: string[];
  cta_label: string;
  success_message: string;
  comment_hint: string;
  ask_phone: boolean;
  ask_comment: boolean;
  allow_multiple_items: boolean;
};

export type ServiceResponse = {
  slug: string;
  status: string;
  config: ServiceConfig;
  my_leads?: CustomerLead[];
};

export type OwnerService = ServiceResponse & {
  link: string;
  new_leads: number;
  lead_count: number;
};

export type OwnerOverview = {
  owner: { name: string; username: string } | null;
  services: OwnerService[];
};

export type Lead = {
  id: string;
  service_title: string;
  service_slug: string;
  customer_name: string;
  phone: string | null;
  item_title: string;
  slot_label: string;
  comment: string;
  status: LeadStatus;
  created_at: string | null;
  scheduled_at: string | null;
  chat_url: string;
};

export class MaxApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "MaxApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const initData = getInitData();
  if (!initData) {
    throw new MaxApiError("Откройте страницу внутри MAX", 401);
  }

  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Max-Init-Data": initData,
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    // A dropped connection inside a messenger webview is common enough that it deserves
    // its own wording rather than a generic failure.
    throw new MaxApiError("Нет связи с сервером", 0);
  }

  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch {
      detail = "";
    }
    throw new MaxApiError(detail || `Ошибка ${response.status}`, response.status);
  }

  // 204 has no body to parse - a delete answers with nothing but its status.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function fetchService(slug: string): Promise<ServiceResponse> {
  return request<ServiceResponse>(`/max/miniapp/service/${encodeURIComponent(slug)}`);
}

export function fetchOwnerOverview(): Promise<OwnerOverview> {
  return request<OwnerOverview>("/max/miniapp/owner/overview");
}

export function fetchOwnerLeads(slug?: string): Promise<{ leads: Lead[] }> {
  const query = slug ? `?slug=${encodeURIComponent(slug)}` : "";
  return request<{ leads: Lead[] }>(`/max/miniapp/owner/leads${query}`);
}

export function setLeadStatus(leadId: string, status: Lead["status"]): Promise<Lead> {
  return request<Lead>(`/max/miniapp/owner/leads/${leadId}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

export type AttachedFile = {
  filename: string;
  content_type: string;
  data_base64: string;
};

export type CreatedService = OwnerService & { used_llm: boolean };

/** One description in, a published storefront out. Takes as long as the model does -
 *  usually 5-10 s - so the caller shows progress rather than a frozen button. */
export function createService(input: {
  brief: string;
  site_url?: string;
  files?: AttachedFile[];
}): Promise<CreatedService> {
  return request<CreatedService>("/max/miniapp/owner/services", {
    method: "POST",
    body: JSON.stringify({
      brief: input.brief,
      site_url: input.site_url || "",
      files: input.files || [],
    }),
  });
}

export function editService(
  slug: string,
  instruction: string
): Promise<OwnerService & { changed: boolean }> {
  return request(`/max/miniapp/owner/services/${encodeURIComponent(slug)}/edit`, {
    method: "POST",
    body: JSON.stringify({ instruction }),
  });
}

export function patchService(
  slug: string,
  payload: {
    title?: string;
    tagline?: string;
    about?: string;
    items?: Array<{ title: string; description?: string; price_rub: number | null }>;
    allow_multiple_items?: boolean;
  }
): Promise<OwnerService> {
  return request(`/max/miniapp/owner/services/${encodeURIComponent(slug)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function setServiceStatus(
  slug: string,
  status: "live" | "disabled"
): Promise<{ slug: string; status: string }> {
  return request(`/max/miniapp/owner/services/${encodeURIComponent(slug)}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

export function deleteService(slug: string): Promise<void> {
  return request<void>(`/max/miniapp/owner/services/${encodeURIComponent(slug)}`, {
    method: "DELETE",
  });
}

export type CreateLeadInput = {
  slug: string;
  item_title?: string;
  items?: Array<{ title: string; quantity: number }>;
  slot_label?: string;
  customer_name?: string;
  phone?: string;
  comment?: string;
};

export function createLead(
  input: CreateLeadInput
): Promise<{ id: string; status: string; success_message: string; item_title: string }> {
  return request("/max/miniapp/lead", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
