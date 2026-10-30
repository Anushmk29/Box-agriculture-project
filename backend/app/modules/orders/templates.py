"""Account-scoped template versions, snapshots, and the procurement review gate."""

from sqlalchemy import func

from app.core.work_limits import WorkLimit
from app.models.excel_template import ExcelTemplate
from app.models.master_data import Customer
from app.services.excel_profiles import is_reference_definition, references


def template_snapshots(db, account_key):
    account_key = account_key or "GENERAL"
    # Serialize initial seeding for the selected account without parser DB access.
    db.query(Customer).filter_by(id=account_key).with_for_update().first()
    versions = (
        db.query(ExcelTemplate.template_key, func.max(ExcelTemplate.version).label("version"))
        .filter(ExcelTemplate.account_key == account_key)
        .group_by(ExcelTemplate.template_key)
        .subquery()
    )
    rows = (
        db.query(ExcelTemplate)
        .join(
            versions,
            (ExcelTemplate.template_key == versions.c.template_key)
            & (ExcelTemplate.version == versions.c.version),
        )
        .filter(ExcelTemplate.account_key == account_key)
        .order_by(ExcelTemplate.template_key)
        .limit(101)
        .all()
    )
    if len(rows) > 100:
        raise WorkLimit("Review at most 100 recurring formats per main customer")
    latest = {}
    for row in rows:
        latest.setdefault(row.template_key, row)
    for definition in references():
        if definition["key"] not in latest:
            row = ExcelTemplate(
                account_key=account_key,
                template_key=definition["key"],
                version=1,
                definition=definition,
            )
            db.add(row)
            latest[definition["key"]] = row
    if len(latest) > 100:
        raise WorkLimit("Review at most 100 recurring formats per main customer")
    db.flush()
    return [
        dict(id=r.id, version=r.version, confirmed=r.confirmed,
             automatic=is_reference_definition(r.definition), definition=r.definition)
        for r in latest.values()
    ]


def recognize_saved_formats(db, raw, drafts):
    """Upgrade pending reference imports without re-reading or changing lines.

    Older uploads required confirmation even for bundled mappings. Verify their
    saved template version belongs to this account and is still an unchanged
    reference before removing that redundant gate. Corrections stay intact.
    """
    ids = {d.metadata["template"].get("id") for d in drafts if d.metadata.get("template")}
    ids.discard(None)
    if not ids:
        return
    rows = {row.id: row for row in db.query(ExcelTemplate).filter(
        ExcelTemplate.id.in_(ids), ExcelTemplate.account_key == (raw.main_customer_id or "GENERAL")
    ).all()}
    for draft in drafts:
        template = draft.metadata.get("template", {})
        row = rows.get(template.get("id"))
        if (row and template.get("status") == "MATCHED"
                and row.template_key == template.get("key")
                and row.version == template.get("version")
                and is_reference_definition(row.definition)):
            template["automatic"] = True


def template_issues(drafts):
    issues = []
    for draft in drafts:
        template = draft.metadata.get("template")
        if not template:
            continue  # Legacy saved orders and PDFs retain their existing workflow.
        if template.get("status") != "MATCHED" or not (template.get("confirmed") or template.get("automatic")):
            issues.append("Review and confirm the customer Excel format before procurement")
        issues.extend(template.get("issues", []))
    return list(dict.fromkeys(issues))


def require_template_review(drafts):
    issues = template_issues(drafts)
    if issues:
        raise ValueError("; ".join(issues))
