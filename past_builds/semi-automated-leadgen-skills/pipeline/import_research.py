"""Attach Repository 2 evidence to unresolved Enrich records; never confirm it."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
from .enrich import enrichment_counts
from .providers.enrichment import ordering_platform_from_answer
from .schema import ONLINE_ORDERING_PLATFORMS, utc_now


def import_answers(payload: dict, research: dict) -> tuple[dict, int]:
    if research.get('field') != 'ordering_platform':
        raise ValueError('This import only accepts ordering_platform research.')
    answers = research.get('answers')
    if not isinstance(answers, list):
        raise ValueError('The file needs an answers list.')
    by_id = {}
    for answer in answers:
        lead_id = answer.get('lead_id')
        verdict = answer.get('verdict', {})
        evidence = answer.get('evidence', {})
        if not lead_id or lead_id in by_id:
            raise ValueError('Each answer needs one unique pipeline lead_id.')
        if type(verdict.get('supportsNoOnlineOrdering')) is not bool:
            raise ValueError('The ordering verdict must be true or false.')
        urls = verdict.get('sourceUrls')
        if not isinstance(urls, list) or not urls or not all(isinstance(u, str) and u.startswith(('https://', 'http://')) for u in urls):
            raise ValueError('Every answer needs its source URLs.')
        if not verdict.get('researchReason') or evidence.get('source_url') not in urls or not evidence.get('quote'):
            raise ValueError('Every answer needs a reason and a cited source excerpt.')
        if answer.get('platform_kind') not in (None, *ONLINE_ORDERING_PLATFORMS):
            raise ValueError('Unknown ordering platform kind.')
        if verdict['supportsNoOnlineOrdering'] and answer.get('platform_kind'):
            raise ValueError('An answer cannot say both no ordering and an online platform.')
        by_id[lead_id] = answer
    result = deepcopy(payload)
    changed = 0
    for record in result['records']:
        answer = by_id.get(record['id'])
        if answer is None or record.get('ordering_platform') is not None:
            continue
        value = ordering_platform_from_answer(answer)
        record['enrichment']['research'] = {
            'field': 'ordering_platform',
            'status': 'awaiting_check' if value is not None else 'unclear',
            'agent': 'research_agent(imported)',
            'verdict': answer['verdict'],
            'proposed_value': value,
            'source_url': answer['evidence']['source_url'],
            'quote': answer['evidence']['quote'],
        }
        changed += 1
    result['counts'] = enrichment_counts(result['records'])
    result['at'] = utc_now()
    return result, changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--answers', required=True)
    parser.add_argument('--in', dest='src', default='out/enriched.json')
    parser.add_argument('--out', default='out/enriched.json')
    args = parser.parse_args(argv)
    try:
        updated, count = import_answers(json.loads(Path(args.src).read_text()), json.loads(Path(args.answers).read_text()))
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, f'Cannot import research: {error}\nRun the normal pipeline first and check the research file.\n')
    Path(args.out).write_text(json.dumps(updated, indent=2) + '\n')
    print(f'Attached evidence to {count} unresolved records. No answer was confirmed; nothing was sent.')
    print('Next: python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery"')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
