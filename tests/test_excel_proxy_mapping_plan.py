from backend.excel_proxy_mapping import build_mapping_plan


class Element:
    def __init__(self, entity="IfcBuildingElementProxy"):
        self.entity = entity

    def is_a(self):
        return self.entity


class Repository:
    def __init__(self, existing=None):
        self.existing = existing or {}
        self.fetches = []
        self.upserts = []

    def fetch_bulk(self, signatures, *, scope):
        self.fetches.append((list(signatures), scope))
        return {key: self.existing[key] for key in signatures if key in self.existing}

    def upsert_bulk(self, mappings, *, scope):
        self.upserts.append((list(mappings), scope))


def _rows(count, **values):
    return [(index, {"GlobalId": f"guid-{index}", **values}, Element()) for index in range(count)]


def _build(rows, inference, repository=None, **kwargs):
    entities = {"IfcBuildingElementProxy", "IfcBeam", "IfcColumn", "IfcWall"}
    return build_mapping_plan(
        rows, entity_is_valid=lambda entity: entity in entities,
        predefined_is_valid=lambda _entity, _predef: True,
        infer=inference, repository=repository, scope={"project": "p", "model": "m"}, **kwargs,
    )


def test_explicit_entity_bypasses_inference():
    calls = []
    plan, metrics = _build(_rows(2, TargetEntity="IfcBeam"), lambda *args: calls.append(args))
    assert calls == []
    assert {row.resolved_target_entity for row in plan} == {"IfcBeam"}
    assert metrics["explicit_excel_entity"] == 2


def test_thousands_of_identical_signatures_resolve_once_without_global_id_keying():
    calls = []

    def infer(signature, row):
        calls.append(signature.value)
        return "IfcBeam"

    plan, metrics = _build(_rows(2000, ObjectType="Steel beam", TypeName="UB 457x191x82"), infer)
    assert len(calls) == 1
    assert metrics["unique_signatures"] == 1
    assert plan[0].resolution_source == "inferred_mapping"
    assert all(row.resolution_source == "signature_cache" for row in plan[1:])


def test_different_signatures_are_inferred_independently_and_failure_is_cached():
    calls = []
    rows = _rows(2, ObjectType="A") + [(2, {"GlobalId": "other", "ObjectType": "B"}, Element())]
    plan, _ = _build(rows, lambda signature, row: calls.append(signature.value) or None)
    assert len(calls) == 2
    assert all(row.resolved_target_entity == "IfcBuildingElementProxy" for row in plan)
    assert plan[1].resolution_source == "signature_cache"


def test_existing_mappings_are_paged_and_new_mappings_bulk_upserted():
    seed_plan, _ = _build(_rows(1, ObjectType="saved"), lambda *_: None)
    signature = seed_plan[0].mapping_signature.value
    repository = Repository({signature: "IfcColumn"})
    rows = _rows(1, ObjectType="saved") + [(1, {"GlobalId": "new", "ObjectType": "new"}, Element())]
    plan, _ = _build(rows, lambda _signature, _row: "IfcWall", repository, page_size=1)
    assert len(repository.fetches) == 2
    assert plan[0].resolution_source == "existing_proxy_mapping"
    assert plan[0].resolved_target_entity == "IfcColumn"
    assert len(repository.upserts) == 1
    assert repository.upserts[0][0][0]["target_entity"] == "IfcWall"


def test_application_needs_only_precomputed_values():
    repository = Repository()
    plan, _ = _build(_rows(3, ObjectType="same"), lambda *_: "IfcBeam", repository)
    calls_before = (len(repository.fetches), len(repository.upserts))
    applied = [(row.global_id, row.resolved_target_entity) for row in plan]
    assert applied == [("guid-0", "IfcBeam"), ("guid-1", "IfcBeam"), ("guid-2", "IfcBeam")]
    assert (len(repository.fetches), len(repository.upserts)) == calls_before


def test_invalid_explicit_entity_safely_falls_back_to_source():
    plan, _ = _build(_rows(1, TargetEntity="IfcNotInSchema"), lambda *_: None)
    assert plan[0].resolution_source == "unchanged_fallback"
    assert plan[0].resolved_target_entity == "IfcBuildingElementProxy"
