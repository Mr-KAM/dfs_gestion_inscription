"""SAS defenses (soutenances): juries of staff members, each hearing a list of SAS students."""
from datetime import timedelta

from app.extensions import db
from app.utils import as_utc, utcnow

JURY_ROLES = {"president": "Président", "technical": "Évaluateur technique",
              "presentation": "Évaluateur présentation"}

# Defense grid (Fiche globale d'évaluation · Soutenance): (section, key, criterion, indicators, weight). Total 100.
DEFENSE_SECTIONS = {"A": "Contenu et maîtrise du sujet", "B": "Qualité de la présentation",
                    "C": "Échanges avec le jury"}
DEFENSE_CRITERIA = (
    ("A", "brief", "Compréhension du brief", "Reformule le problème, le contexte agricole et les attentes", 10),
    ("A", "solution", "Pertinence de la solution",
     "Réponse techniquement et agronomiquement juste, réaliste sur le terrain", 15),
    ("A", "results", "Résultats et livrable", "Montre ce qui a été produit : données, carte, outil, prototype", 15),
    ("A", "method", "Démarche et sources", "Explique comment il a travaillé ; cite ses données et sources", 5),
    ("B", "structure", "Structure et clarté", "Plan annoncé et suivi, enchaînements logiques, conclusion nette", 10),
    ("B", "visuals", "Supports visuels", "Lisibles, sobres, utiles ; schémas et chiffres bien choisis", 5),
    ("B", "speech", "Expression orale", "Voix audible, débit maîtrisé, vocabulaire technique juste", 10),
    ("B", "posture", "Posture et contact", "Regarde le public, ne lit pas ses notes, assurance", 5),
    ("B", "timing", "Gestion du temps", "Respecte le temps imparti, équilibre entre les parties", 5),
    ("C", "answers", "Justesse des réponses", "Répond précisément, reconnaît ce qu'il ne sait pas", 15),
    ("C", "listening", "Écoute et réactivité", "Comprend la question, accepte la critique, rebondit", 5),
)
SECTION_WEIGHTS = {s: sum(c[4] for c in DEFENSE_CRITERIA if c[0] == s) for s in DEFENSE_SECTIONS}
NOT_APPLICABLE = "na"
DEFENSE_SCALE = (("1", "Insuffisant", "Attendu non atteint"),
                 ("2", "À renforcer", "Atteint en partie, lacunes visibles"),
                 ("3", "Satisfaisant", "Atteint, clair et solide"), ("4", "Très bon", "Remarquable, convaincant"),
                 (NOT_APPLICABLE, "Sans objet", "Critère non applicable (ex. démo absente)"))
SUPPORTS = {"slides": "Diaporama", "demo": "Démo logiciel / app", "map": "Carte / SIG", "poster": "Poster",
            "video": "Vidéo", "sample": "Échantillon terrain"}
POSITIVES = {"plan": "Annonce un plan clair", "field": "Exemples de terrain", "figures": "Chiffres et unités justes",
             "readable": "Visuels lisibles de loin", "demo_ok": "Démo qui fonctionne",
             "conclusion": "Conclusion forte",
             "vocabulary": "Vocabulaire agronomique juste", "shared": "Parole bien répartie (équipe)"}
NEGATIVES = {"reads": "Lit ses diapositives", "crowded": "Diapositives surchargées", "overtime": "Dépasse le temps",
             "fast": "Débit trop rapide", "off_topic": "Hors sujet par moments", "demo_ko": "Démo en échec",
             "typos": "Fautes sur les supports", "one_speaker": "Un seul membre parle (équipe)"}
DECISIONS = {"admitted": "Admis", "reserved": "Admis sous réserve", "waitlist": "Liste d'attente",
             "rejected": "Non admis"}


def grid_points(ratings: dict, key: str, weight: int) -> float | None:
    """Points = weight x rating / 4; None if not rated or N/A."""
    rating = (ratings or {}).get(key, "")
    return weight * int(rating) / 4 if rating in ("1", "2", "3", "4") else None


class Defense(db.Model):
    __tablename__ = "defenses"

    id = db.Column(db.Integer, primary_key=True)
    sas_id = db.Column(db.Integer, db.ForeignKey("sas.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True))
    location = db.Column(db.String(150), default="")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    # Sent by the admin from the global jury: until then the students see nothing of this defense.
    results_sent_at = db.Column(db.DateTime(timezone=True))

    sas = db.relationship("Sas")
    juries = db.relationship("Jury", back_populates="defense", cascade="all, delete-orphan", order_by="Jury.id")


class Jury(db.Model):
    __tablename__ = "juries"

    id = db.Column(db.Integer, primary_key=True)
    defense_id = db.Column(db.Integer, db.ForeignKey("defenses.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    room = db.Column(db.String(100), default="")
    starts_at = db.Column(db.DateTime(timezone=True))  # None: the defense's start
    duration = db.Column(db.Integer, nullable=False, default=30)  # minutes per student
    # Deliberation validated by the admin: sheets locked, the jury's grades are final.
    validated_at = db.Column(db.DateTime(timezone=True))
    validated_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    # Shared timer set by a jury member: running until timer_ends_at, or paused with timer_left seconds.
    timer_ends_at = db.Column(db.DateTime(timezone=True))
    timer_left = db.Column(db.Integer)

    defense = db.relationship("Defense", back_populates="juries")
    members = db.relationship("JuryMember", back_populates="jury", cascade="all, delete-orphan",
                              order_by="JuryMember.id")
    candidates = db.relationship("JuryCandidate", back_populates="jury", cascade="all, delete-orphan",
                                 order_by="JuryCandidate.position")

    @property
    def start(self):
        return as_utc(self.starts_at or self.defense.starts_at)

    def slot(self, index: int):
        """Passage time of the index-th student (0-based), None without a start time."""
        return self.start + timedelta(minutes=self.duration * index) if self.start else None

    def timer_state(self) -> tuple[str, int]:
        """(running | paused | idle, remaining seconds; negative once over time)."""
        if self.timer_ends_at:
            return "running", int((as_utc(self.timer_ends_at) - utcnow()).total_seconds())
        if self.timer_left is not None:
            return "paused", self.timer_left
        return "idle", 0

    def member_of(self, user) -> "JuryMember | None":
        return next((m for m in self.members if m.user_id == user.id), None)


class JuryMember(db.Model):
    """A staff member in a jury. defense_id is repeated so the database refuses one person in two juries."""
    __tablename__ = "jury_members"

    id = db.Column(db.Integer, primary_key=True)
    jury_id = db.Column(db.Integer, db.ForeignKey("juries.id", ondelete="CASCADE"), nullable=False, index=True)
    defense_id = db.Column(db.Integer, db.ForeignKey("defenses.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default="technical")

    jury = db.relationship("Jury", back_populates="members")
    user = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("defense_id", "user_id", name="uq_jury_members_defense_user"),)

    @property
    def role_label(self) -> str:
        return JURY_ROLES.get(self.role, self.role)


class JuryCandidate(db.Model):
    """A student defending before a jury (one jury per student and defense), in passage order."""
    __tablename__ = "jury_candidates"

    id = db.Column(db.Integer, primary_key=True)
    jury_id = db.Column(db.Integer, db.ForeignKey("juries.id", ondelete="CASCADE"), nullable=False, index=True)
    defense_id = db.Column(db.Integer, db.ForeignKey("defenses.id", ondelete="CASCADE"), nullable=False)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False,
                             index=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    # Shared part of the defense sheet: subject, times, format, observations, appraisal;
    # decision and decision_note are set by the admin only, in the global jury.
    sheet = db.Column(db.JSON, nullable=False, default=dict, server_default="{}")

    jury = db.relationship("Jury", back_populates="candidates")
    candidate = db.relationship("Candidate")
    scores = db.relationship("DefenseScore", back_populates="row", cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("defense_id", "candidate_id", name="uq_jury_candidates_defense_candidate"),)


class DefenseScore(db.Model):
    """One jury member's ratings for one student, on the whole grid whatever his role in the jury."""
    __tablename__ = "defense_scores"

    id = db.Column(db.Integer, primary_key=True)
    jury_candidate_id = db.Column(db.Integer, db.ForeignKey("jury_candidates.id", ondelete="CASCADE"),
                                  nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ratings = db.Column(db.JSON, nullable=False, default=dict)
    evidence = db.Column(db.JSON, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    row = db.relationship("JuryCandidate", back_populates="scores")

    __table_args__ = (db.UniqueConstraint("jury_candidate_id", "user_id", name="uq_defense_scores_row_user"),)

    def section(self, section: str) -> tuple[float, int]:
        """(points, applicable weight) of one section; (0, 0) if nothing rated in it."""
        rated = [(p, c[4]) for c in DEFENSE_CRITERIA
                 if c[0] == section and (p := grid_points(self.ratings, c[1], c[4])) is not None]
        return sum(p for p, _ in rated), sum(w for _, w in rated)
