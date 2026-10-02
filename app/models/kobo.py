from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db
from app.utils import utcnow

MATCH_MATCHED = "matched"
MATCH_MANUAL = "manual"
MATCH_AMBIGUOUS = "ambiguous"
MATCH_UNMATCHED = "unmatched"

JSONType = db.JSON().with_variant(JSONB(), "postgresql")


class KoboSubmission(db.Model):
    __tablename__ = "kobo_submissions"

    id = db.Column(db.Integer, primary_key=True)
    kobo_id = db.Column(db.String(64), unique=True, nullable=False)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="SET NULL"), index=True)
    match_status = db.Column(db.String(20), nullable=False, default=MATCH_UNMATCHED, index=True)
    match_field = db.Column(db.String(30))
    file_status = db.Column(db.String(20))
    submitted_at = db.Column(db.DateTime(timezone=True))
    data = db.Column(JSONType, nullable=False, default=dict)
    synced_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    candidate = db.relationship("Candidate", back_populates="kobo_submissions")
