-- Support urgency-first queues with the newest case first inside each urgency.

create index if not exists reports_queue_status_newest_order
  on reports (
    status,
    (case urgency
      when 'critical'::urgency then 4
      when 'high'::urgency then 3
      when 'medium'::urgency then 2
      else 1
    end) desc,
    created_at desc,
    id desc
  );

create index if not exists reports_queue_all_newest_order
  on reports (
    (case urgency
      when 'critical'::urgency then 4
      when 'high'::urgency then 3
      when 'medium'::urgency then 2
      else 1
    end) desc,
    created_at desc,
    id desc
  );
