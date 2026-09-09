-- Separate setup scope assurance from live FireTrace delivery.
alter table public.projects add column if not exists setup_completed_at timestamptz;
alter table public.projects add column if not exists setup_completed_by uuid references auth.users(id);

create table if not exists public.firetrace_tracker_items (
 id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
 ifc_file_id uuid references public.ifc_files(id) on delete set null, ifc_object_id uuid references public.ifc_objects(id) on delete set null,
 ifc_global_id text, space_id uuid references public.project_spaces(id) on delete set null, storey_id uuid references public.building_storeys(id) on delete set null,
 scope_source_id uuid not null references public.fire_strategy_reviews(id) on delete restrict,
 scope_status text not null check(scope_status in ('IN_SCOPE','OUT_OF_SCOPE','REVIEW_REQUIRED')),
 categories text[] not null default '{}', requirement_reference text, title text not null, description text,
 tracker_status text not null default 'OPEN' check(tracker_status in ('OPEN','IN_PROGRESS','READY_FOR_REVIEW','COMPLETE','ON_HOLD','CLOSED')),
 compliance_status text not null default 'NOT_ASSESSED' check(compliance_status in ('NOT_ASSESSED','PARTIAL','COMPLIANT','NON_COMPLIANT','NOT_APPLICABLE')),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(project_id, scope_source_id)
);

create table if not exists public.firetrace_evidence_templates (
 id uuid primary key default gen_random_uuid(), name text not null, description text, active boolean not null default true,
 project_id uuid references public.projects(id) on delete cascade, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.firetrace_evidence_template_items (
 id uuid primary key default gen_random_uuid(), template_id uuid not null references public.firetrace_evidence_templates(id) on delete cascade,
 category text not null, evidence_type text not null, title text not null, description text, required boolean not null default true,
 sort_order integer not null default 0, unique(template_id, category, evidence_type)
);

create table if not exists public.firetrace_evidence_requirements (
 id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
 tracker_item_id uuid not null references public.firetrace_tracker_items(id) on delete restrict,
 template_item_id uuid references public.firetrace_evidence_template_items(id) on delete set null,
 evidence_type text not null, title text not null, description text, required boolean not null default true,
 status text not null default 'OPEN' check(status in ('OPEN','IN_PROGRESS','SUBMITTED','UNDER_REVIEW','ACCEPTED','RETURNED','NOT_REQUIRED')),
 assigned_to_user_id uuid references auth.users(id), assigned_by_user_id uuid references auth.users(id), assigned_at timestamptz,
 responsible_organisation text, due_at timestamptz, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index if not exists firetrace_requirements_assignee_idx on public.firetrace_evidence_requirements(assigned_to_user_id,status,due_at);

create table if not exists public.firetrace_evidence_submissions (
 id uuid primary key, project_id uuid not null references public.projects(id) on delete cascade,
 evidence_requirement_id uuid not null references public.firetrace_evidence_requirements(id) on delete restrict,
 tracker_item_id uuid not null references public.firetrace_tracker_items(id) on delete restrict,
 submitted_by_user_id uuid not null references auth.users(id), submitted_at timestamptz not null default now(),
 storage_bucket text not null default 'project-files', storage_path text not null unique, original_filename text not null,
 mime_type text, file_size bigint not null check(file_size >= 0), sha256 text, description text, version_number integer not null,
 review_status text not null default 'SUBMITTED' check(review_status in ('SUBMITTED','UNDER_REVIEW','ACCEPTED','RETURNED')),
 reviewed_by_user_id uuid references auth.users(id), reviewed_at timestamptz, review_comment text,
 supersedes_submission_id uuid references public.firetrace_evidence_submissions(id), created_at timestamptz not null default now(),
 unique(evidence_requirement_id,version_number)
);

create table if not exists public.firetrace_audit_events (
 id bigint generated always as identity primary key, project_id uuid not null references public.projects(id) on delete cascade,
 actor_user_id uuid references auth.users(id), event_type text not null, entity_type text not null, entity_id text not null,
 before_state jsonb, after_state jsonb, created_at timestamptz not null default now()
);

-- Idempotent, deterministic setup completion. Suggestions without a confirmed
-- scope decision never enter the operational tracker.
create or replace function public.complete_firetrace_setup(target_project_id uuid) returns integer
language plpgsql security invoker set search_path=public as $$
declare created_count integer;
begin
 if not public.can_manage_project(target_project_id) then raise exception 'Project administration permission required'; end if;
 if exists(select 1 from fire_strategy_reviews where project_id=target_project_id and not orphaned
           and relevance='NOT_ASSESSED') then raise exception 'Assess every candidate before completing setup'; end if;
 if exists(select 1 from fire_strategy_reviews where project_id=target_project_id and not orphaned
           and relevance='IN_SCOPE' and coalesce(array_length(categories,1),0)=0) then raise exception 'Every in-scope item requires a confirmed category'; end if;
 insert into firetrace_tracker_items(project_id,ifc_file_id,ifc_object_id,ifc_global_id,storey_id,scope_source_id,scope_status,categories,requirement_reference,title,description)
 select r.project_id,r.model_id,r.ifc_object_id,r.ifc_global_id,o.storey_id,r.id,'IN_SCOPE',r.categories,r.requirement_reference,
        coalesce(nullif(o.name,''),r.entity_type||' '||r.ifc_global_id),r.review_notes
 from fire_strategy_reviews r left join ifc_objects o on o.id=r.ifc_object_id
 where r.project_id=target_project_id and not r.orphaned and r.relevance='IN_SCOPE'
 on conflict(project_id,scope_source_id) do update set scope_status='IN_SCOPE',categories=excluded.categories,
 requirement_reference=excluded.requirement_reference,title=excluded.title,description=excluded.description,updated_at=now();
 get diagnostics created_count=row_count;
 update projects set setup_completed_at=coalesce(setup_completed_at,now()),setup_completed_by=coalesce(setup_completed_by,auth.uid()),project_status='ACTIVE' where id=target_project_id;
 insert into firetrace_audit_events(project_id,actor_user_id,event_type,entity_type,entity_id,after_state)
 values(target_project_id,auth.uid(),'SETUP_COMPLETED','PROJECT',target_project_id::text,jsonb_build_object('tracker_items_upserted',created_count));
 return created_count;
end $$;

create or replace view public.firetrace_project_metrics with (security_invoker=true) as
select p.id project_id, count(distinct ti.id) filter(where ti.scope_status='IN_SCOPE') in_scope_tracker_items,
 count(er.id) filter(where er.required and er.status not in ('ACCEPTED','NOT_REQUIRED')) open_evidence_requirements,
 count(es.id) filter(where es.review_status='SUBMITTED') evidence_submitted,
 count(es.id) filter(where es.review_status='ACCEPTED') evidence_accepted,
 count(es.id) filter(where es.review_status='RETURNED') evidence_returned,
 count(er.id) filter(where er.due_at<now() and er.status not in ('ACCEPTED','NOT_REQUIRED')) overdue_tasks,
 (select count(*) from fire_strategy_reviews r where r.project_id=p.id and not r.orphaned and r.relevance in ('NOT_ASSESSED','REVIEW_REQUIRED')) unresolved_scope_items,
 coalesce(round(100.0*count(er.id) filter(where er.required and er.status='ACCEPTED')/nullif(count(er.id) filter(where er.required),0)),0) compliance_percent
from projects p left join firetrace_tracker_items ti on ti.project_id=p.id left join firetrace_evidence_requirements er on er.tracker_item_id=ti.id
left join firetrace_evidence_submissions es on es.evidence_requirement_id=er.id group by p.id;

-- RLS reflects operational roles: members read; assigned contributors update
-- their tasks/submissions; editors/reviewers/admins perform assurance.
do $$ declare t text; begin foreach t in array array['firetrace_tracker_items','firetrace_evidence_templates','firetrace_evidence_template_items','firetrace_evidence_requirements','firetrace_evidence_submissions','firetrace_audit_events'] loop execute format('alter table public.%I enable row level security',t); end loop; end $$;
create policy tracker_read on public.firetrace_tracker_items for select using(public.is_project_member(project_id));
create policy tracker_manage on public.firetrace_tracker_items for all using(public.can_edit_project(project_id)) with check(public.can_edit_project(project_id));
create policy evidence_requirements_read on public.firetrace_evidence_requirements for select using(public.is_project_member(project_id));
create policy evidence_requirements_manage on public.firetrace_evidence_requirements for all using(public.can_edit_project(project_id)) with check(public.can_edit_project(project_id));
create policy evidence_requirements_assignee_update on public.firetrace_evidence_requirements for update using(assigned_to_user_id=auth.uid()) with check(assigned_to_user_id=auth.uid());
create policy submissions_read on public.firetrace_evidence_submissions for select using(public.is_project_member(project_id));
create policy submissions_add on public.firetrace_evidence_submissions for insert with check(submitted_by_user_id=auth.uid() and exists(select 1 from firetrace_evidence_requirements r where r.id=evidence_requirement_id and r.assigned_to_user_id=auth.uid()));
create policy submissions_review on public.firetrace_evidence_submissions for update using(public.can_edit_project(project_id)) with check(public.can_edit_project(project_id));
create policy audit_read on public.firetrace_audit_events for select using(public.is_project_member(project_id));
create policy audit_add on public.firetrace_audit_events for insert with check(actor_user_id=auth.uid() and public.is_project_member(project_id));
create policy templates_read on public.firetrace_evidence_templates for select using(project_id is null or public.is_project_member(project_id));
create policy templates_manage on public.firetrace_evidence_templates for all using(project_id is not null and public.can_manage_project(project_id)) with check(project_id is not null and public.can_manage_project(project_id));
create policy template_items_read on public.firetrace_evidence_template_items for select using(exists(select 1 from firetrace_evidence_templates t where t.id=template_id and (t.project_id is null or public.is_project_member(t.project_id))));

-- Canonical path: projects/{project_id}/evidence/{tracker_item_id}/{evidence_requirement_id}/{submission_id}/{filename}.
-- Storage keys are parsed as projects/{uuid}/evidence/... and never made public.
create policy firetrace_evidence_storage_read on storage.objects for select to authenticated
 using(bucket_id='project-files' and (storage.foldername(name))[1]='projects' and (storage.foldername(name))[3]='evidence' and public.is_project_member(((storage.foldername(name))[2])::uuid));
create policy firetrace_evidence_storage_insert on storage.objects for insert to authenticated
 with check(bucket_id='project-files' and (storage.foldername(name))[1]='projects' and (storage.foldername(name))[3]='evidence'
 and exists(select 1 from public.firetrace_evidence_requirements r where r.project_id=((storage.foldername(name))[2])::uuid and r.id=((storage.foldername(name))[5])::uuid and r.assigned_to_user_id=auth.uid()));

do $$ declare t text; begin foreach t in array array['firetrace_tracker_items','firetrace_evidence_templates','firetrace_evidence_template_items','firetrace_evidence_requirements','firetrace_evidence_submissions','firetrace_audit_events'] loop execute format('grant select,insert,update on public.%I to authenticated',t); end loop; end $$;
grant select on public.firetrace_project_metrics to authenticated;
grant execute on function public.complete_firetrace_setup(uuid) to authenticated;

create or replace function public.can_review_project(target_project uuid) returns boolean language sql stable security definer
set search_path=public as $$ select public.is_platform_admin() or exists(select 1 from public.project_members where project_id=target_project and user_id=auth.uid() and role in ('OWNER','ADMIN','EDITOR','REVIEWER')) $$;
drop policy submissions_review on public.firetrace_evidence_submissions;
create policy submissions_review on public.firetrace_evidence_submissions for update using(public.can_review_project(project_id)) with check(public.can_review_project(project_id));
grant execute on function public.can_review_project(uuid) to authenticated;

-- Append-only audit events make assignment, submission and review changes
-- notification-ready without coupling delivery state to scope state.
create or replace function public.audit_firetrace_change() returns trigger language plpgsql security definer set search_path=public as $$
begin
 insert into firetrace_audit_events(project_id,actor_user_id,event_type,entity_type,entity_id,before_state,after_state)
 values(coalesce(new.project_id,old.project_id),auth.uid(),tg_argv[0],tg_table_name,coalesce(new.id,old.id)::text,
        case when tg_op='INSERT' then null else to_jsonb(old) end,case when tg_op='DELETE' then null else to_jsonb(new) end);
 return coalesce(new,old);
end $$;
drop trigger if exists firetrace_requirement_audit on public.firetrace_evidence_requirements;
create trigger firetrace_requirement_audit after insert or update on public.firetrace_evidence_requirements for each row execute function public.audit_firetrace_change('TASK_CHANGED');
drop trigger if exists firetrace_submission_audit on public.firetrace_evidence_submissions;
create trigger firetrace_submission_audit after insert or update on public.firetrace_evidence_submissions for each row execute function public.audit_firetrace_change('EVIDENCE_CHANGED');
drop trigger if exists fire_strategy_scope_audit on public.fire_strategy_reviews;
create trigger fire_strategy_scope_audit after update on public.fire_strategy_reviews for each row when(old.relevance is distinct from new.relevance) execute function public.audit_firetrace_change('SCOPE_CHANGED');
create policy template_items_manage on public.firetrace_evidence_template_items for all
 using(exists(select 1 from public.firetrace_evidence_templates t where t.id=template_id and t.project_id is not null and public.can_manage_project(t.project_id)))
 with check(exists(select 1 from public.firetrace_evidence_templates t where t.id=template_id and t.project_id is not null and public.can_manage_project(t.project_id)));
