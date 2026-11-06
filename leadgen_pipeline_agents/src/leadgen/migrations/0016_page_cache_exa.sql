-- Exa's get_contents is now the primary page backend; its pages are cached like the others.
ALTER TABLE page_cache DROP CONSTRAINT page_cache_backend_check,
    ADD CONSTRAINT page_cache_backend_check CHECK (backend IN ('exa', 'firecrawl', 'free'));
