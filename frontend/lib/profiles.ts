import { apiFetch } from "./api";

export type Technician = {
  id: string;
  display_name: string;
};

type TechnicianList = {
  items: Technician[];
};

export async function listTechnicians(
  accessToken: string,
): Promise<Technician[]> {
  const result = await apiFetch<TechnicianList>(
    "/profiles/technicians",
    accessToken,
  );
  return result.items;
}
