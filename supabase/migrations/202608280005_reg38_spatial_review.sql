-- Spatial review is project configuration: only owners/admins may change working rows.
drop policy if exists project_spaces_update on public.project_spaces;
drop policy if exists project_spaces_delete on public.project_spaces;
drop policy if exists project_zones_update on public.project_zones;
drop policy if exists project_zones_delete on public.project_zones;
drop policy if exists project_zones_insert on public.project_zones;
drop policy if exists zone_members_insert on public.project_zone_members;
drop policy if exists zone_members_delete on public.project_zone_members;

create policy project_spaces_update on public.project_spaces for update to authenticated
using(public.can_manage_project(project_id)) with check(public.can_manage_project(project_id));
create policy project_spaces_delete on public.project_spaces for delete to authenticated using(public.can_manage_project(project_id));
create policy project_zones_update on public.project_zones for update to authenticated
using(public.can_manage_project(project_id)) with check(public.can_manage_project(project_id));
create policy project_zones_delete on public.project_zones for delete to authenticated using(public.can_manage_project(project_id));
create policy project_zones_insert on public.project_zones for insert to authenticated with check(public.can_manage_project(project_id));
create policy zone_members_insert on public.project_zone_members for insert to authenticated with check
(exists(select 1 from public.project_zones z where z.id=zone_id and public.can_manage_project(z.project_id)));
create policy zone_members_delete on public.project_zone_members for delete to authenticated using
(exists(select 1 from public.project_zones z where z.id=zone_id and public.can_manage_project(z.project_id)));
