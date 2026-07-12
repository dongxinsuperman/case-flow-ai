from sqlalchemy.dialects.postgresql import JSONB

from app.models.requirements import RequirementGroup, RequirementPool


def test_requirement_json_fields_match_jsonb_migrations():
    assert isinstance(RequirementPool.__table__.c.source_payload.type, JSONB)
    assert isinstance(RequirementGroup.__table__.c.function_map_files.type, JSONB)
