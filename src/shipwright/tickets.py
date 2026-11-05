"""v1 location: re-exports `shipwright.domain.tickets` (removed in phase B)."""

from shipwright.domain.tickets import (  # noqa: F401
    Ticket,
    TicketNotFound,
    branch_root,
    extract_acceptance_criteria,
    extract_spec_refs,
    parse_ticket_key,
)
