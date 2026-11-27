"""Record a contact fact you have checked against a cited source, without guessing."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .schema import OWNER_ROLE_LADDER, ENRICHED_FIELDS, derive_columns, utc_now
from .enrich import enrichment_counts


def review_contact(payload, *, lead_name, field, value, source_url, reviewed_by):
    if field not in ('owner_name', 'owner_role', 'email', 'location_count'):
        raise ValueError('Use review_research for ordering evidence.')
    if not reviewed_by.strip() or not source_url.startswith(('https://', 'http://')):
        raise ValueError('Provide your name and the source URL you checked.')
    if field == 'location_count':
        value = int(value)
        if value < 0: raise ValueError('Shop count cannot be negative.')
    elif field == 'owner_role' and value not in OWNER_ROLE_LADDER:
        raise ValueError('Use a course role: ' + ', '.join(OWNER_ROLE_LADDER))
    elif field == 'email' and ('@' not in value or ' ' in value):
        raise ValueError('Enter the email exactly as shown in the checked source.')
    elif not str(value).strip():
        raise ValueError('A checked value cannot be blank.')
    matches = [r for r in payload['records'] if r['name'].casefold() == lead_name.casefold()]
    if len(matches) != 1: raise ValueError('Name must match exactly one bakery record.')
    record = matches[0]
    record[field] = value
    record.update(derive_columns(record))
    record['enrichment']['complete'] = all(record.get(f) is not None for f in ENRICHED_FIELDS)
    record['enrichment'].setdefault('contact_checks', []).append({'field': field, 'value': value, 'source_url': source_url, 'reviewed_by': reviewed_by, 'at': utc_now()})
    payload['counts'] = enrichment_counts(payload['records'])
    payload['at'] = utc_now()
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--in', dest='src', default='out/enriched.json')
    parser.add_argument('--out', default='out/enriched.json')
    for name in ('lead', 'field', 'value', 'source-url', 'reviewed-by'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        payload = review_contact(json.loads(Path(args.src).read_text()), lead_name=args.lead, field=args.field, value=args.value, source_url=args.source_url, reviewed_by=args.reviewed_by)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, f'Contact fact not recorded: {error}\n')
    Path(args.out).write_text(json.dumps(payload, indent=2) + '\n')
    print('Saved your checked contact fact with its source. Nothing was qualified or sent.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
