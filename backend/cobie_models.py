from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class CobieIssue(BaseModel):
    issue_id: str
    severity: str
    sheet_name: str
    row_number: int
    column_name: str
    cell_reference: str
    rule_id: str
    rule_type: str
    message: str
    expected_value: str = ""
    actual_value: str = ""
    recommendation: str = ""
    related_sheet: Optional[str] = None
    related_column: Optional[str] = None

class CobieSheetSummary(BaseModel):
    sheet_name: str
    rows_checked: int = 0
    errors: int = 0
    warnings: int = 0
    info: int = 0
    status: str = "Pass"

class CobieRuleSummary(BaseModel):
    rule_id: str
    rule_name: str
    count: int
    severity: str

class CobieValidationSummary(BaseModel):
    overall_status: str
    total_issues: int
    errors: int
    warnings: int
    info: int
    sheets_checked: int
    rows_checked: int
    rules_executed: int

class CobieValidationResult(BaseModel):
    job_id: str = ""
    status: str = "complete"
    original_filename: str = ""
    rule_pack: str
    validation_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    processing_duration_seconds: float = 0
    summary: CobieValidationSummary
    sheet_summary: List[CobieSheetSummary]
    rule_summary: List[CobieRuleSummary]
    issues: List[CobieIssue]
    exports: Dict[str, str] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
