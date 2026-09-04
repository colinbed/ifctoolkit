-- Automatic detection is a suggestion, never an assurance decision.
alter table public.fire_strategy_reviews
  add column if not exists suggested_categories text[] not null default '{}';

alter table public.fire_strategy_reviews
  alter column relevance set default 'REVIEW_REQUIRED';

-- Only reconcile untouched automatic rows. A reviewer identity, manual
-- selection, or progressed review status is treated as an explicit decision.
update public.fire_strategy_reviews
set suggested_categories = case
      when cardinality(suggested_categories) = 0 then categories
      else suggested_categories
    end,
    categories = '{}',
    relevance = 'REVIEW_REQUIRED'
where automatically_suggested
  and not manually_selected
  and reviewed_by is null
  and review_status = 'NOT_STARTED';
