-- The sourcing agent may add Maps cells to an approved plan, choosing only from the market's
-- Census cities and the category's keywords. `origin` records who proposed each cell.
ALTER TABLE scrape_plan_cells
    ADD COLUMN origin text NOT NULL DEFAULT 'planned' CHECK (origin IN ('planned', 'agent'));
