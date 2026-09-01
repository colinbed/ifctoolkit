do $$
declare health jsonb;
begin
  if (select count(*) from information_schema.columns where table_schema='public'
      and table_name='project_spaces' and column_name='working_fields_edited'
      and data_type='boolean' and is_nullable='NO') <> 1 then
    raise exception 'project_spaces.working_fields_edited contract is absent';
  end if;
  if (select column_default from information_schema.columns where table_schema='public'
      and table_name='project_spaces' and column_name='working_fields_edited') <> 'false' then
    raise exception 'working_fields_edited compatibility default is not false';
  end if;
  if to_regclass('public.fire_strategy_reviews') is null then
    raise exception 'fire_strategy_reviews is absent';
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.fire_strategy_reviews'::regclass
      and contype='u' and pg_get_constraintdef(oid) like '%(project_id, model_id, ifc_global_id)%') then
    raise exception 'Fire Strategy retry identity is absent';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid='public.fire_strategy_reviews'::regclass
      and tgname='fire_strategy_reviews_set_updated_at' and not tgisinternal) then
    raise exception 'Fire Strategy updated_at trigger is absent';
  end if;
  if (select count(*) from pg_policies where schemaname='public'
      and tablename='fire_strategy_reviews') <> 2 then
    raise exception 'Fire Strategy RLS policies are incomplete';
  end if;
  select public.reg38_schema_health() into health;
  if not (health->>'valid')::boolean then
    raise exception 'schema health failed after reconciliation: %', health;
  end if;
end $$;

select working_fields_edited from public.project_spaces limit 1;
select * from public.fire_strategy_reviews limit 1;
