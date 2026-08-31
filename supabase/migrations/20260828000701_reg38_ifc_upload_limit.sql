-- Keep the private Regulation 38 bucket aligned with the browser and server limit.
update storage.buckets
set file_size_limit = 524288000,
    allowed_mime_types = array['application/octet-stream', 'application/x-step', 'application/step']
where id = 'reg38-evidence';
