ALTER TABLE budget_reservations DROP CONSTRAINT budget_reservations_status_check,
    ADD CONSTRAINT budget_reservations_status_check
    CHECK (status IN ('started','succeeded','uncertain','rejected'));
