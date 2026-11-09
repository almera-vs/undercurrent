-- One row per report (upsert semantics: latest ingest of a report_id wins), with the
-- Python-parsed classification / insider fields and the resolved instrument.
with latest as (
    select *
    from {{ ref('bronze_reports') }}
    qualify row_number() over (partition by report_id order by ingested_at desc) = 1
)
select
    r.report_id, r.site, r.geru_id, r.system, r.kind, r.report_number,
    r.issuer_name, r.isin as listed_isin, r.title, r.subject, r.legal_basis, r.body_text,
    r.attachments_json, r.template, r.extract_notes,
    r.published_at, r.published_at_local, r.known_at, r.ingested_at,
    r.source_url, r.content_sha256,
    f.signal_type, f.subtype, f.class_rule,
    f.insider_person, f.insider_role, f.insider_side, f.insider_volume,
    f.insider_avg_price, f.insider_value, f.insider_trade_date, f.insider_venue,
    f.insider_pct_free_float, f.insider_parse_notes,
    i.resolved_isin as isin, i.resolve_method, i.resolve_score, i.market,
    i.short_name, i.ticker
from latest r
left join {{ work_file('silver/report_fields.parquet') }} f using (report_id)
left join {{ work_file('silver/report_instrument.parquet') }} i using (report_id)
