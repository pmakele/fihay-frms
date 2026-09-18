from __future__ import annotations

from datetime import date, timedelta, datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4
import zipfile
import os
import base64
import hashlib
import hmac
import secrets
import json
from contextvars import ContextVar

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, func, inspect, text, event
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, with_loader_criteria, Session

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "fihay_records.db"
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(DATA_DIR / "uploads"))).resolve()
RECEIPTS_DIR = UPLOADS_DIR / "receipts"
INCIDENTS_DIR = UPLOADS_DIR / "incidents"
BACKUPS_DIR = DATA_DIR / "backups"
RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

_engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()

CURRENT_FARM_ID: ContextVar[int | None] = ContextVar("current_farm_id", default=None)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    full_name = Column(String(160), default="")
    password_hash = Column(String(255), nullable=False)
    active = Column(Integer, default=1)
    is_system_admin = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FarmMembership(Base):
    __tablename__ = "farm_memberships"
    __table_args__ = (UniqueConstraint("user_id", "farm_id", name="uq_user_farm"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False, index=True)
    role = Column(String(40), default="Farmer")
    active = Column(Integer, default=1)
    user = relationship("User")
    farm = relationship("Farm")


class Farm(Base):
    __tablename__ = "farms"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    location = Column(String(150), default="")
    total_area_ha = Column(Float, default=0)
    notes = Column(Text, default="")


class Plot(Base):
    __tablename__ = "plots"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    name = Column(String(80), nullable=False)
    area_ha = Column(Float, default=0)
    land_use = Column(String(100), default="")
    irrigation_method = Column(String(80), default="")
    notes = Column(Text, default="")
    farm = relationship("Farm")


class CropCycle(Base):
    __tablename__ = "crop_cycles"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    plot_id = Column(Integer, ForeignKey("plots.id"), nullable=True)
    crop = Column(String(100), nullable=False)
    variety = Column(String(100), default="")
    area_ha = Column(Float, default=0)
    planting_date = Column(Date, nullable=True)
    duration_days = Column(Integer, nullable=True)
    expected_harvest_date = Column(Date, nullable=True)
    status = Column(String(40), default="Planned")
    notes = Column(Text, default="")
    farm = relationship("Farm")
    plot = relationship("Plot")
    weekly_updates = relationship("CropWeeklyUpdate", back_populates="crop_cycle", cascade="all, delete-orphan")
    applications = relationship("CropApplication", back_populates="crop_cycle", cascade="all, delete-orphan")

    @property
    def label(self):
        bits = [self.crop]
        if self.variety:
            bits.append(self.variety)
        if self.plot:
            bits.append(self.plot.name)
        return " • ".join(bits)


class CropWeeklyUpdate(Base):
    __tablename__ = "crop_weekly_updates"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False, index=True)
    crop_cycle_id = Column(Integer, ForeignKey("crop_cycles.id"), nullable=False)
    update_date = Column(Date, nullable=False, default=date.today)
    growth_stage = Column(String(100), default="")
    crop_condition = Column(String(100), default="")
    pests_diseases = Column(Text, default="")
    action_taken = Column(Text, default="")
    notes = Column(Text, default="")
    crop_cycle = relationship("CropCycle", back_populates="weekly_updates")

    @property
    def week_number(self):
        if self.crop_cycle and self.crop_cycle.planting_date and self.update_date:
            return max(1, ((self.update_date - self.crop_cycle.planting_date).days // 7) + 1)
        return None


class CropApplication(Base):
    __tablename__ = "crop_applications"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False, index=True)
    crop_cycle_id = Column(Integer, ForeignKey("crop_cycles.id"), nullable=False)
    application_date = Column(Date, nullable=False, default=date.today)
    application_type = Column(String(40), nullable=False)  # Fertiliser or Pesticide
    product = Column(String(150), nullable=False)
    quantity = Column(Float, nullable=True)
    unit = Column(String(40), default="")
    rate = Column(String(100), default="")
    method = Column(String(100), default="")
    reason = Column(String(200), default="")
    notes = Column(Text, default="")
    crop_cycle = relationship("CropCycle", back_populates="applications")


class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    crop_cycle_id = Column(Integer, ForeignKey("crop_cycles.id"), nullable=True)
    expense_date = Column(Date, nullable=False, default=date.today)
    category = Column(String(80), nullable=False)
    description = Column(String(200), nullable=False)
    supplier = Column(String(120), default="")
    amount = Column(Float, nullable=False, default=0)
    payment_method = Column(String(60), default="")
    reference = Column(String(100), default="")
    receipt_filename = Column(String(240), default="")
    receipt_original_name = Column(String(240), default="")
    notes = Column(Text, default="")
    crop_cycle = relationship("CropCycle")


class Sale(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    crop_cycle_id = Column(Integer, ForeignKey("crop_cycles.id"), nullable=True)
    sale_type = Column(String(30), nullable=False, default="Crop")
    livestock_group_id = Column(Integer, ForeignKey("livestock.id"), nullable=True)
    livestock_animal_id = Column(Integer, ForeignKey("livestock_animals.id"), nullable=True)
    livestock_sex = Column(String(20), default="Unknown")
    produce_movement_id = Column(Integer, ForeignKey("produce_movements.id"), nullable=True)
    livestock_event_id = Column(Integer, ForeignKey("livestock_events.id"), nullable=True)
    sale_date = Column(Date, nullable=False, default=date.today)
    buyer = Column(String(120), nullable=False)
    product = Column(String(120), nullable=False)
    quantity = Column(Float, nullable=False, default=0)
    unit = Column(String(30), default="kg")
    unit_price = Column(Float, nullable=False, default=0)
    amount_paid = Column(Float, nullable=False, default=0)
    reference = Column(String(100), default="")
    notes = Column(Text, default="")
    crop_cycle = relationship("CropCycle")
    livestock_group = relationship("Livestock", foreign_keys=[livestock_group_id])
    livestock_animal = relationship("LivestockAnimal", foreign_keys=[livestock_animal_id])

    @property
    def total(self):
        return (self.quantity or 0) * (self.unit_price or 0)

    @property
    def balance(self):
        return max(0, self.total - (self.amount_paid or 0))


class Livestock(Base):
    __tablename__ = "livestock"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    batch_name = Column(String(100), nullable=False)
    species = Column(String(80), nullable=False)
    breed = Column(String(100), default="")
    quantity = Column(Integer, nullable=False, default=0)  # opening/base quantity for backwards compatibility
    male_count = Column(Integer, default=0)
    female_count = Column(Integer, default=0)
    unknown_count = Column(Integer, default=0)
    acquisition_date = Column(Date, nullable=True)
    status = Column(String(40), default="Active")
    notes = Column(Text, default="")
    animals = relationship("LivestockAnimal", back_populates="group", cascade="all, delete-orphan")
    events = relationship("LivestockEvent", back_populates="group", cascade="all, delete-orphan")

    def current_total(self):
        return max(0, (self.quantity or 0) + sum((e.quantity_change or 0) for e in self.events))

    def current_male(self):
        return max(0, (self.male_count or 0) + sum((e.male_change or 0) for e in self.events))

    def current_female(self):
        return max(0, (self.female_count or 0) + sum((e.female_change or 0) for e in self.events))

    def current_unknown(self):
        base_allocated = (self.male_count or 0) + (self.female_count or 0) + (self.unknown_count or 0)
        base_unallocated = max(0, (self.quantity or 0) - base_allocated)
        return max(0, (self.unknown_count or 0) + base_unallocated + sum((e.unknown_change or 0) for e in self.events))


class LivestockAnimal(Base):
    __tablename__ = "livestock_animals"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False, index=True)
    group_id = Column(Integer, ForeignKey("livestock.id"), nullable=False)
    animal_code = Column(String(80), nullable=False)
    sex = Column(String(20), default="Unknown")
    birth_or_acquired_date = Column(Date, nullable=True)
    status = Column(String(40), default="Active")
    pregnancy_status = Column(String(40), default="Not Pregnant")
    mating_date = Column(Date, nullable=True)
    expected_birth_date = Column(Date, nullable=True)
    last_birth_date = Column(Date, nullable=True)
    dam = Column(String(80), default="")
    sire = Column(String(80), default="")
    notes = Column(Text, default="")
    group = relationship("Livestock", back_populates="animals")
    events = relationship("LivestockEvent", back_populates="animal")


class LivestockEvent(Base):
    __tablename__ = "livestock_events"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False, index=True)
    group_id = Column(Integer, ForeignKey("livestock.id"), nullable=False)
    animal_id = Column(Integer, ForeignKey("livestock_animals.id"), nullable=True)
    event_date = Column(Date, nullable=False, default=date.today)
    event_type = Column(String(50), nullable=False)
    affected_sex = Column(String(20), default="Unknown")
    quantity = Column(Integer, default=0)
    quantity_change = Column(Integer, default=0)
    male_change = Column(Integer, default=0)
    female_change = Column(Integer, default=0)
    unknown_change = Column(Integer, default=0)
    product_or_medicine = Column(String(160), default="")
    symptoms_diagnosis = Column(Text, default="")
    dosage = Column(String(100), default="")
    provider = Column(String(120), default="")
    next_due_date = Column(Date, nullable=True)
    mating_date = Column(Date, nullable=True)
    gestation_days = Column(Integer, nullable=True)
    expected_birth_date = Column(Date, nullable=True)
    birth_male = Column(Integer, default=0)
    birth_female = Column(Integer, default=0)
    birth_unknown = Column(Integer, default=0)
    cause_of_death = Column(String(200), default="")
    outcome_status = Column(String(80), default="")
    notes = Column(Text, default="")
    group = relationship("Livestock", back_populates="events")
    animal = relationship("LivestockAnimal", back_populates="events")


class MasterData(Base):
    __tablename__ = "master_data"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False, index=True)
    category = Column(String(80), nullable=False, index=True)
    name = Column(String(140), nullable=False)
    code = Column(String(60), default="")
    active = Column(Integer, default=1)
    notes = Column(Text, default="")


class Harvest(Base):
    __tablename__ = "harvests"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    crop_cycle_id = Column(Integer, ForeignKey("crop_cycles.id"), nullable=False)
    harvest_date = Column(Date, nullable=False, default=date.today)
    quantity_harvested = Column(Float, nullable=False, default=0)
    unit = Column(String(30), default="kg")
    grade = Column(String(60), default="")
    reject_qty = Column(Float, default=0)
    marketable_qty = Column(Float, nullable=False, default=0)
    notes = Column(Text, default="")
    crop_cycle = relationship("CropCycle")


class ProduceMovement(Base):
    __tablename__ = "produce_movements"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    crop_cycle_id = Column(Integer, ForeignKey("crop_cycles.id"), nullable=False)
    harvest_id = Column(Integer, ForeignKey("harvests.id"), nullable=True)
    movement_date = Column(Date, nullable=False, default=date.today)
    movement_type = Column(String(50), nullable=False)
    quantity_change = Column(Float, nullable=False, default=0)
    unit = Column(String(30), default="kg")
    reference = Column(String(120), default="")
    notes = Column(Text, default="")
    crop_cycle = relationship("CropCycle")
    harvest = relationship("Harvest")


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    item_name = Column(String(140), nullable=False)
    category = Column(String(80), default="")
    item_type = Column(String(30), nullable=False, default="Durable")
    opening_qty = Column(Float, nullable=False, default=0)
    unit = Column(String(30), default="unit")
    location = Column(String(120), default="")
    condition = Column(String(60), default="Good")
    acquisition_date = Column(Date, nullable=True)
    unit_cost = Column(Float, nullable=True)
    min_stock_level = Column(Float, nullable=True)
    supplier = Column(String(120), default="")
    reference = Column(String(120), default="")
    last_service_date = Column(Date, nullable=True)
    next_service_date = Column(Date, nullable=True)
    notes = Column(Text, default="")
    transactions = relationship("InventoryTransaction", back_populates="item", cascade="all, delete-orphan")

    def current_qty(self):
        return max(0, (self.opening_qty or 0) + sum((x.quantity_change or 0) for x in self.transactions))

    def issued_qty(self):
        if self.item_type != "Durable":
            return 0
        issued = sum((x.quantity or 0) for x in self.transactions if x.transaction_type == "Issue/Use")
        returned = sum((x.quantity or 0) for x in self.transactions if x.transaction_type == "Return")
        return max(0, issued - returned)

    def available_qty(self):
        return max(0, self.current_qty() - self.issued_qty()) if self.item_type == "Durable" else self.current_qty()


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    transaction_date = Column(Date, nullable=False, default=date.today)
    transaction_type = Column(String(50), nullable=False)
    quantity = Column(Float, nullable=False, default=0)
    quantity_change = Column(Float, nullable=False, default=0)
    issued_to = Column(String(120), default="")
    from_to_location = Column(String(120), default="")
    reference = Column(String(120), default="")
    notes = Column(Text, default="")
    item = relationship("InventoryItem", back_populates="transactions")


class Incident(Base):
    __tablename__ = "incidents"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    incident_date = Column(Date, nullable=False, default=date.today)
    category = Column(String(90), nullable=False)
    severity = Column(String(30), default="Medium")
    title = Column(String(180), nullable=False)
    description = Column(Text, nullable=False)
    plot_id = Column(Integer, ForeignKey("plots.id"), nullable=True)
    crop_cycle_id = Column(Integer, ForeignKey("crop_cycles.id"), nullable=True)
    livestock_group_id = Column(Integer, ForeignKey("livestock.id"), nullable=True)
    livestock_animal_id = Column(Integer, ForeignKey("livestock_animals.id"), nullable=True)
    water_source_id = Column(Integer, ForeignKey("water_sources.id"), nullable=True)
    immediate_action = Column(Text, default="")
    follow_up_action = Column(Text, default="")
    responsible_person = Column(String(120), default="")
    status = Column(String(40), default="Open")
    resolution_date = Column(Date, nullable=True)
    cost_loss_estimate = Column(Float, default=0)
    lessons_learned = Column(Text, default="")
    attachment_filename = Column(String(240), default="")
    attachment_original_name = Column(String(240), default="")
    notes = Column(Text, default="")
    plot = relationship("Plot")
    crop_cycle = relationship("CropCycle")
    livestock_group = relationship("Livestock", foreign_keys=[livestock_group_id])
    livestock_animal = relationship("LivestockAnimal", foreign_keys=[livestock_animal_id])
    water_source = relationship("WaterSource", foreign_keys=[water_source_id])


class KnowledgeArticle(Base):
    __tablename__ = "knowledge_articles"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    title = Column(String(180), nullable=False)
    category = Column(String(90), default="General")
    body = Column(Text, nullable=False)
    tags = Column(String(250), default="")
    source_incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=True)
    active = Column(Integer, default=1)


class WaterSource(Base):
    __tablename__ = "water_sources"
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False)
    name = Column(String(100), nullable=False)
    source_type = Column(String(80), nullable=False)
    yield_m3_hr = Column(Float, nullable=True)
    storage_litres = Column(Float, nullable=True)
    status = Column(String(50), default="Active")
    notes = Column(Text, default="")


DEFAULT_MASTER_DATA = {
    "Crop": ["Watermelon", "Maize", "Chillies", "Mushrooms", "Rape", "Lettuce", "Cabbage", "Broccoli", "Cauliflower"],
    "Crop Variety": ["Crimson Sweet"],
    "Unit": ["kg", "tonne", "head", "bundle", "bag", "unit", "litre", "ml", "g", "tray", "crate", "hour", "day"],
    "Expense Category": ["Seed/Spawn", "Fertiliser/Manure", "Crop Protection", "Animal Feed", "Animal Health", "Labour", "Water/Irrigation", "Fuel/Transport", "Packaging", "Repairs", "Other"],
    "Payment Method": ["Business Account", "Cash", "Mobile Money", "Owner Contribution"],
    "Irrigation Method": ["Drip", "Sprinkler", "Hand watering", "Other"],
    "Water Source Type": ["Borehole", "Tank", "Reservoir", "Municipal", "Other"],
    "Livestock Species": ["Goat", "Chicken", "Guinea Fowl", "Cattle", "Sheep", "Pig", "Rabbit", "Other"],
    "Livestock Breed": ["Boer", "Indigenous", "Other"],
    "Vaccine/Medicine": ["Other"],
    "Labour Task": ["Land Preparation", "Planting", "Weeding", "Irrigation", "Spraying", "Harvesting", "Packing", "General Farm Work"],
    "Asset Category": ["Pump", "Tank", "Solar Equipment", "Irrigation Equipment", "Machinery", "Tool", "Structure", "Vehicle", "Other"],
    "Livestock Event Type": ["Purchase", "Mating", "Pregnancy", "Birth", "Vaccination", "Deworming", "Sickness", "Treatment", "Recovery", "Death", "Sale", "Transfer In", "Transfer Out"],
    "Incident Category": ["Pest Outbreak", "Crop Disease", "Livestock Health", "Irrigation Failure", "Equipment Breakdown", "Water Problem", "Weather Damage", "Theft/Security", "Safety", "Other"],
    "Inventory Category": ["Hand Tool", "Equipment", "Irrigation", "Seed", "Fertiliser", "Pesticide", "Animal Feed", "Packaging", "Fuel", "Other"],
    "Buyer Type": ["Individual", "Retailer", "Wholesaler", "Aggregator", "Institution", "Exporter", "Other"],
    "Growth Stage": ["Nursery", "Germination", "Vegetative", "Flowering", "Fruit Set", "Maturing", "Harvesting"],
    "Fertiliser": ["Manure", "NPK 2:3:2", "NPK 5:1:5", "Urea"],
    "Pesticide": ["Cypermethrin"],
}

FARM_SCOPED_MODELS = [
    Plot, CropCycle, CropWeeklyUpdate, CropApplication, Expense, Sale, Livestock,
    LivestockAnimal, LivestockEvent, MasterData, Harvest, ProduceMovement,
    InventoryItem, InventoryTransaction, Incident, KnowledgeArticle, WaterSource
]


@event.listens_for(Session, "do_orm_execute")
def _tenant_filter(execute_state):
    farm_id = CURRENT_FARM_ID.get()
    if not farm_id or not execute_state.is_select or execute_state.execution_options.get("skip_tenant_filter"):
        return
    stmt = execute_state.statement
    def tenant_rule(fid):
        return lambda cls: cls.farm_id == fid
    for model in FARM_SCOPED_MODELS:
        stmt = stmt.options(with_loader_criteria(
            model, tenant_rule(farm_id), include_aliases=True
        ))
    execute_state.statement = stmt


@event.listens_for(Session, "before_flush")
def _set_tenant_on_new(session, flush_context, instances):
    farm_id = CURRENT_FARM_ID.get()
    if not farm_id:
        return
    for obj in session.new:
        if type(obj) in FARM_SCOPED_MODELS and getattr(obj, "farm_id", None) in (None, 0):
            obj.farm_id = farm_id


def seed_master_data(db, farm_id: int):
    for category, values in DEFAULT_MASTER_DATA.items():
        existing = {x.name.lower() for x in db.query(MasterData).execution_options(skip_tenant_filter=True).filter(
            MasterData.farm_id == farm_id, MasterData.category == category).all()}
        for value in values:
            if value.lower() not in existing:
                db.add(MasterData(farm_id=farm_id, category=category, name=value, active=1))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    rounds = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, rounds_s, salt_s, digest_s = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_s.encode())
        expected = base64.urlsafe_b64decode(digest_s.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds_s))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


Base.metadata.create_all(bind=engine)


app = FastAPI(title="FiHay Farm Record Management System - Hosted Pilot")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

PUBLIC_PATHS = {"/login", "/register", "/health", "/manifest.webmanifest", "/sw.js"}

@app.middleware("http")
async def authentication_and_tenant(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(f"/login?next={path}", status_code=303)

    with SessionLocal() as db:
        user = db.query(User).filter(User.id == user_id, User.active == 1).first()
        if not user:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)

        memberships = db.query(FarmMembership).filter(
            FarmMembership.user_id == user.id, FarmMembership.active == 1
        ).all()
        allowed_ids = {m.farm_id for m in memberships}
        active_farm_id = request.session.get("farm_id")

        if user.is_system_admin:
            if not active_farm_id:
                first_farm = db.query(Farm).order_by(Farm.id).first()
                active_farm_id = first_farm.id if first_farm else None
        elif active_farm_id not in allowed_ids:
            active_farm_id = memberships[0].farm_id if memberships else None

        if active_farm_id:
            request.session["farm_id"] = int(active_farm_id)
        elif path not in {"/onboarding", "/logout"} and not path.startswith("/platform"):
            return RedirectResponse("/onboarding", status_code=303)

        active_membership = next((m for m in memberships if m.farm_id == active_farm_id), None)
        role = "Platform Admin" if user.is_system_admin else (active_membership.role if active_membership else "")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and role == "Viewer" and not path.startswith("/switch-farm/"):
            return JSONResponse({"detail": "This account has view-only access."}, status_code=403)
        if path.startswith("/admin/") and not user.is_system_admin and role not in {"Owner", "Manager"}:
            return RedirectResponse("/", status_code=303)

        request.state.user = user
        request.state.memberships = memberships
        request.state.farm_id = int(active_farm_id) if active_farm_id else None
        request.state.role = role

    token = CURRENT_FARM_ID.set(request.state.farm_id)
    try:
        return await call_next(request)
    finally:
        CURRENT_FARM_ID.reset(token)

# SessionMiddleware is intentionally added after the auth middleware so the signed
# session is available before tenant resolution runs.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "change-this-secret-before-production"),
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "0") == "1",
    max_age=60 * 60 * 24 * 30,
)


def parse_date(value: str | None) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def master_dict():
    with SessionLocal() as db:
        rows = db.query(MasterData).filter(MasterData.active == 1).order_by(MasterData.category, MasterData.name).all()
        out = {}
        for row in rows:
            out.setdefault(row.category, []).append(row.name)
        return out


def ctx(request: Request, **kwargs):
    farm = None
    farm_choices = []
    farm_id = getattr(request.state, "farm_id", None)
    user = getattr(request.state, "user", None)
    with SessionLocal() as db:
        if farm_id:
            farm = db.get(Farm, farm_id)
        if user:
            if user.is_system_admin:
                farm_choices = [(f.id, f.name, "Platform Admin") for f in db.query(Farm).order_by(Farm.name).all()]
            else:
                farm_choices = [(f.id, f.name, m.role) for m, f in db.query(FarmMembership, Farm).join(
                    Farm, Farm.id == FarmMembership.farm_id).filter(
                    FarmMembership.user_id == user.id, FarmMembership.active == 1).order_by(Farm.name).all()]
    return {
        "request": request,
        "master": master_dict(),
        "current_user": user,
        "current_farm": farm,
        "memberships": getattr(request.state, "memberships", []),
        "farm_choices": farm_choices,
        "current_role": getattr(request.state, "role", ""),
        **kwargs,
    }

def current_farm(db):
    farm_id = CURRENT_FARM_ID.get()
    return db.get(Farm, farm_id) if farm_id else None

def produce_balance(db, crop_cycle_id: int):
    return float(db.query(func.coalesce(func.sum(ProduceMovement.quantity_change), 0)).filter(
        ProduceMovement.crop_cycle_id == crop_cycle_id).scalar() or 0)

def inventory_change(transaction_type: str, quantity: float, item_type: str = "Consumable"):
    q = max(0.0, float(quantity or 0))
    if item_type == "Durable":
        if transaction_type in {"Receipt/Purchase", "Transfer In"}: return q
        if transaction_type in {"Damage/Loss", "Write-off", "Transfer Out"}: return -q
        if transaction_type in {"Issue/Use", "Return"}: return 0.0
        return float(quantity or 0)
    if transaction_type in {"Receipt/Purchase", "Return", "Transfer In"}: return q
    if transaction_type in {"Issue/Use", "Damage/Loss", "Write-off", "Transfer Out"}: return -q
    return float(quantity or 0)


def calc_harvest(planting: Optional[date], duration_days: Optional[int]) -> Optional[date]:
    if planting and duration_days is not None and duration_days >= 0:
        return planting + timedelta(days=duration_days)
    return None


ALLOWED_RECEIPT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}


async def save_receipt(upload: UploadFile | None):
    if not upload or not upload.filename:
        return "", ""
    ext = Path(upload.filename).suffix.lower()
    if ext not in ALLOWED_RECEIPT_EXTENSIONS:
        raise ValueError("Receipt must be JPG, JPEG, PNG, WEBP or PDF.")
    safe_name = f"{uuid4().hex}{ext}"
    path = RECEIPTS_DIR / safe_name
    content = await upload.read()
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("File is too large. Maximum size is 10 MB.")
    path.write_bytes(content)
    return safe_name, Path(upload.filename).name


async def save_incident_attachment(upload: UploadFile | None):
    if not upload or not upload.filename:
        return "", ""
    ext = Path(upload.filename).suffix.lower()
    if ext not in ALLOWED_RECEIPT_EXTENSIONS:
        raise ValueError("Attachment must be JPG, JPEG, PNG, WEBP or PDF.")
    safe_name = f"{uuid4().hex}{ext}"
    path = INCIDENTS_DIR / safe_name
    content = await upload.read()
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("File is too large. Maximum size is 10 MB.")
    path.write_bytes(content)
    return safe_name, Path(upload.filename).name


def calculate_livestock_event_changes(event_type: str, quantity: int, affected_sex: str,
                                       birth_male: int, birth_female: int, birth_unknown: int):
    q_change = male = female = unknown = 0
    et = (event_type or "").strip()
    sex = affected_sex or "Unknown"

    if et == "Birth":
        male, female, unknown = max(0, birth_male), max(0, birth_female), max(0, birth_unknown)
        q_change = male + female + unknown
    elif et in {"Purchase", "Transfer In"}:
        q_change = max(0, quantity)
        if sex == "Male": male = q_change
        elif sex == "Female": female = q_change
        else: unknown = q_change
    elif et in {"Death", "Sale", "Transfer Out"}:
        q_change = -max(0, quantity)
        if sex == "Male": male = q_change
        elif sex == "Female": female = q_change
        else: unknown = q_change
    return q_change, male, female, unknown


def sync_animal_from_events(db, animal: LivestockAnimal | None):
    if not animal:
        return
    status = animal.status or "Active"
    pregnancy = "Not Pregnant"
    mating_date = None
    expected_birth = None
    last_birth = animal.last_birth_date
    events = db.query(LivestockEvent).filter(LivestockEvent.animal_id == animal.id).order_by(
        LivestockEvent.event_date.asc(), LivestockEvent.id.asc()).all()
    for e in events:
        if e.event_type in {"Mating", "Pregnancy"}:
            pregnancy = "Pregnant" if e.event_type == "Pregnancy" else "Mated"
            mating_date = e.mating_date or e.event_date
            expected_birth = e.expected_birth_date
        elif e.event_type == "Birth":
            pregnancy = "Not Pregnant"
            last_birth = e.event_date
            expected_birth = None
        elif e.event_type == "Death":
            status = "Dead"
        elif e.event_type == "Sale":
            status = "Sold"
        elif e.event_type == "Transfer Out":
            status = "Transferred"
    animal.pregnancy_status = pregnancy
    animal.mating_date = mating_date
    animal.expected_birth_date = expected_birth
    animal.last_birth_date = last_birth
    animal.status = status



@app.get("/uploads/receipts/{filename}")
def protected_receipt(filename: str):
    safe = Path(filename).name
    with SessionLocal() as db:
        row = db.query(Expense).filter(Expense.receipt_filename == safe).first()
        if not row:
            return JSONResponse({"detail": "Not found"}, status_code=404)
    path = RECEIPTS_DIR / safe
    if not path.exists():
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(path)


@app.get("/uploads/incidents/{filename}")
def protected_incident_attachment(filename: str):
    safe = Path(filename).name
    with SessionLocal() as db:
        row = db.query(Incident).filter(Incident.attachment_filename == safe).first()
        if not row:
            return JSONResponse({"detail": "Not found"}, status_code=404)
    path = INCIDENTS_DIR / safe
    if not path.exists():
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(path)


# -------------------- Authentication / multi-farm --------------------
def _normalise_email(value: str) -> str:
    return (value or "").strip().lower()


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


@app.get("/api/context")
def api_context(request: Request):
    user = getattr(request.state, "user", None)
    return {
        "user_id": user.id if user else None,
        "farm_id": getattr(request.state, "farm_id", None),
        "farm_name": current_farm_name(getattr(request.state, "farm_id", None)),
    }


def current_farm_name(farm_id: int | None) -> str:
    if not farm_id:
        return ""
    with SessionLocal() as db:
        farm = db.get(Farm, farm_id)
        return farm.name if farm else ""


@app.get("/health")
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if request.session.get("user_id"):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": "", "next": _safe_next(next)})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")):
    email_n = _normalise_email(email)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email_n, User.active == 1).first()
        if not user or not verify_password(password, user.password_hash):
            return templates.TemplateResponse(request, "login.html", {
                "request": request, "error": "Email or password is incorrect.", "next": _safe_next(next), "email": email_n
            }, status_code=400)
        membership = db.query(FarmMembership).filter(
            FarmMembership.user_id == user.id, FarmMembership.active == 1
        ).order_by(FarmMembership.id).first()
        request.session.clear()
        request.session["user_id"] = user.id
        if membership:
            request.session["farm_id"] = membership.farm_id
        elif user.is_system_admin:
            farm = db.query(Farm).order_by(Farm.id).first()
            if farm:
                request.session["farm_id"] = farm.id
    return RedirectResponse(_safe_next(next), status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if os.getenv("ALLOW_REGISTRATION", "1") != "1":
        return templates.TemplateResponse(request, "register.html", {
            "request": request, "error": "New farmer registration is currently closed.", "registration_closed": True
        }, status_code=403)
    return templates.TemplateResponse(request, "register.html", {"request": request, "error": "", "registration_closed": False})


@app.post("/register", response_class=HTMLResponse)
def register_submit(request: Request, full_name: str = Form(...), email: str = Form(...), password: str = Form(...),
                    farm_name: str = Form(...), location: str = Form(""), total_area_ha: float = Form(0)):
    if os.getenv("ALLOW_REGISTRATION", "1") != "1":
        return templates.TemplateResponse(request, "register.html", {
            "request": request, "error": "New farmer registration is currently closed.", "registration_closed": True
        }, status_code=403)
    email_n = _normalise_email(email)
    if len(password) < 8:
        return templates.TemplateResponse(request, "register.html", {
            "request": request, "error": "Use a password with at least 8 characters.", "registration_closed": False,
            "full_name": full_name, "email": email_n, "farm_name": farm_name, "location": location, "total_area_ha": total_area_ha
        }, status_code=400)
    with SessionLocal() as db:
        if db.query(User).filter(User.email == email_n).first():
            return templates.TemplateResponse(request, "register.html", {
                "request": request, "error": "An account already exists for that email address.", "registration_closed": False,
                "full_name": full_name, "email": email_n, "farm_name": farm_name, "location": location, "total_area_ha": total_area_ha
            }, status_code=400)
        user = User(email=email_n, full_name=full_name.strip(), password_hash=hash_password(password), active=1)
        db.add(user); db.flush()
        farm = Farm(name=farm_name.strip(), location=location.strip(), total_area_ha=max(0, total_area_ha),
                    notes="Created through FiHay FRMS hosted pilot registration")
        db.add(farm); db.flush()
        db.add(FarmMembership(user_id=user.id, farm_id=farm.id, role="Owner", active=1))
        seed_master_data(db, farm.id)
        db.commit()
        request.session.clear(); request.session["user_id"] = user.id; request.session["farm_id"] = farm.id
    return RedirectResponse("/", status_code=303)


@app.get("/logout", response_class=HTMLResponse)
def logout(request: Request):
    request.session.clear()
    return HTMLResponse("""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head><body><p>Signing out…</p><script>if(navigator.serviceWorker&&navigator.serviceWorker.controller){navigator.serviceWorker.controller.postMessage({type:'CLEAR_CACHE'});}setTimeout(()=>location.replace('/login'),250);</script></body></html>""")


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding_page(request: Request):
    return templates.TemplateResponse(request, "onboarding.html", {"request": request, "current_user": getattr(request.state, "user", None), "error": ""})


@app.post("/onboarding")
def onboarding_submit(request: Request, farm_name: str = Form(...), location: str = Form(""), total_area_ha: float = Form(0)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with SessionLocal() as db:
        farm = Farm(name=farm_name.strip(), location=location.strip(), total_area_ha=max(0, total_area_ha))
        db.add(farm); db.flush()
        db.add(FarmMembership(user_id=user.id, farm_id=farm.id, role="Owner", active=1))
        seed_master_data(db, farm.id); db.commit(); request.session["farm_id"] = farm.id
    return RedirectResponse("/", status_code=303)


@app.post("/switch-farm")
def switch_farm_form(request: Request, farm_id: int = Form(...)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with SessionLocal() as db:
        allowed = bool(user.is_system_admin) or db.query(FarmMembership).filter(
            FarmMembership.user_id == user.id, FarmMembership.farm_id == farm_id, FarmMembership.active == 1
        ).first() is not None
        if allowed and db.get(Farm, farm_id):
            request.session["farm_id"] = farm_id
    return RedirectResponse("/", status_code=303)


@app.post("/switch-farm/{farm_id}")
def switch_farm(farm_id: int, request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with SessionLocal() as db:
        allowed = bool(user.is_system_admin) or db.query(FarmMembership).filter(
            FarmMembership.user_id == user.id, FarmMembership.farm_id == farm_id, FarmMembership.active == 1
        ).first() is not None
        if allowed and db.get(Farm, farm_id):
            request.session["farm_id"] = farm_id
    return RedirectResponse("/", status_code=303)


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    return templates.TemplateResponse(request, "account.html", ctx(request, message="", error=""))


@app.post("/account/profile", response_class=HTMLResponse)
def account_profile(request: Request, full_name: str = Form(...)):
    user_id = getattr(request.state, "user", None).id
    with SessionLocal() as db:
        user = db.get(User, user_id); user.full_name = full_name.strip(); db.commit()
    return templates.TemplateResponse(request, "account.html", ctx(request, message="Profile updated.", error=""))


@app.post("/account/password", response_class=HTMLResponse)
def account_password(request: Request, current_password: str = Form(...), new_password: str = Form(...)):
    user_id = getattr(request.state, "user", None).id
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if not verify_password(current_password, user.password_hash):
            return templates.TemplateResponse(request, "account.html", ctx(request, message="", error="Current password is incorrect."), status_code=400)
        if len(new_password) < 8:
            return templates.TemplateResponse(request, "account.html", ctx(request, message="", error="New password must have at least 8 characters."), status_code=400)
        user.password_hash = hash_password(new_password); db.commit()
    return templates.TemplateResponse(request, "account.html", ctx(request, message="Password changed.", error=""))


@app.get("/platform/farms", response_class=HTMLResponse)
def platform_farms(request: Request):
    user = getattr(request.state, "user", None)
    if not user or not user.is_system_admin:
        return RedirectResponse("/", status_code=303)
    with SessionLocal() as db:
        farms = db.query(Farm).order_by(Farm.name).all()
        counts = {f.id: db.query(FarmMembership).filter(FarmMembership.farm_id == f.id, FarmMembership.active == 1).count() for f in farms}
        return templates.TemplateResponse(request, "platform_farms.html", ctx(request, farms=farms, counts=counts))


# Optional platform administrator seeded from environment variables.
def ensure_system_admin():
    email = _normalise_email(os.getenv("ADMIN_EMAIL", ""))
    password = os.getenv("ADMIN_PASSWORD", "")
    if not email or not password:
        return
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(email=email, full_name=os.getenv("ADMIN_NAME", "Platform Administrator"),
                        password_hash=hash_password(password), active=1, is_system_admin=1)
            db.add(user); db.commit()
        elif not user.is_system_admin:
            user.is_system_admin = 1; db.commit()


ensure_system_admin()

# -------------------- Dashboard --------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with SessionLocal() as db:
        farm = current_farm(db)
        total_expenses = db.query(func.coalesce(func.sum(Expense.amount), 0)).scalar() or 0
        sales = db.query(Sale).order_by(Sale.sale_date.desc()).all()
        total_sales = sum(s.total for s in sales)
        total_paid = sum(s.amount_paid or 0 for s in sales)
        outstanding = sum(s.balance for s in sales)
        active_crops = db.query(CropCycle).filter(CropCycle.status.in_(["Planned", "Nursery", "Growing", "Harvesting"])).count()
        groups = db.query(Livestock).all()
        livestock_qty = sum(g.current_total() for g in groups)
        plot_count = db.query(Plot).count()
        water_count = db.query(WaterSource).count()
        inventory_items = db.query(InventoryItem).all()
        low_stock_count = sum(1 for x in inventory_items if x.min_stock_level is not None and x.current_qty() <= x.min_stock_level)
        open_incidents = db.query(Incident).filter(Incident.status.in_(["Open", "Monitoring"])).count()
        recent_incidents = db.query(Incident).order_by(Incident.incident_date.desc(), Incident.id.desc()).limit(5).all()
        recent_expenses = db.query(Expense).order_by(Expense.expense_date.desc(), Expense.id.desc()).limit(5).all()
        recent_sales = sales[:5]
        upcoming_health = db.query(LivestockEvent).filter(
            LivestockEvent.next_due_date.is_not(None), LivestockEvent.next_due_date >= date.today()
        ).order_by(LivestockEvent.next_due_date.asc()).limit(5).all()
        return templates.TemplateResponse(request, "dashboard.html", ctx(request,
            farm=farm, total_expenses=total_expenses, total_sales=total_sales, total_paid=total_paid,
            outstanding=outstanding, net_cash=total_paid-total_expenses, active_crops=active_crops,
            livestock_qty=livestock_qty, plot_count=plot_count, water_count=water_count,
            inventory_count=len(inventory_items), low_stock_count=low_stock_count, open_incidents=open_incidents,
            recent_expenses=recent_expenses, recent_sales=recent_sales, upcoming_health=upcoming_health, recent_incidents=recent_incidents))


# -------------------- Plots --------------------
@app.get("/plots", response_class=HTMLResponse)
def plots(request: Request):
    with SessionLocal() as db:
        farm = current_farm(db)
        rows = db.query(Plot).order_by(Plot.name).all()
        return templates.TemplateResponse(request, "plots.html", ctx(request, farm=farm, rows=rows, edit_plot=None))


@app.post("/plots")
def add_plot(name: str = Form(...), area_ha: float = Form(0), land_use: str = Form(""),
             irrigation_method: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        farm = current_farm(db)
        db.add(Plot(farm_id=farm.id, name=name, area_ha=area_ha, land_use=land_use,
                    irrigation_method=irrigation_method, notes=notes))
        db.commit()
    return RedirectResponse("/plots", status_code=303)


@app.get("/plots/{plot_id}/edit", response_class=HTMLResponse)
def edit_plot_page(plot_id: int, request: Request):
    with SessionLocal() as db:
        farm = current_farm(db)
        rows = db.query(Plot).order_by(Plot.name).all()
        edit_plot = db.query(Plot).filter(Plot.id == plot_id).first()
        if not edit_plot:
            return RedirectResponse("/plots", status_code=303)
        return templates.TemplateResponse(request, "plots.html", ctx(request, farm=farm, rows=rows, edit_plot=edit_plot))


@app.post("/plots/{plot_id}/edit")
def update_plot(plot_id: int, name: str = Form(...), area_ha: float = Form(0), land_use: str = Form(""),
                irrigation_method: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        plot = db.get(Plot, plot_id)
        if plot:
            plot.name, plot.area_ha, plot.land_use = name, area_ha, land_use
            plot.irrigation_method, plot.notes = irrigation_method, notes
            db.commit()
    return RedirectResponse("/plots", status_code=303)


# -------------------- Crops --------------------
@app.get("/crops", response_class=HTMLResponse)
def crops(request: Request):
    with SessionLocal() as db:
        farm = current_farm(db)
        rows = db.query(CropCycle).order_by(CropCycle.id.desc()).all()
        plots = db.query(Plot).order_by(Plot.name).all()
        return templates.TemplateResponse(request, "crops.html", ctx(request, farm=farm, rows=rows, plots=plots, edit_crop=None))


@app.post("/crops")
def add_crop(crop: str = Form(...), variety: str = Form(""), area_ha: float = Form(0), plot_id: str = Form(""),
             planting_date: str = Form(""), duration_days: str = Form(""), status: str = Form("Planned"), notes: str = Form("")):
    with SessionLocal() as db:
        farm = current_farm(db)
        planting = parse_date(planting_date)
        duration = to_int(duration_days, None) if duration_days != "" else None
        db.add(CropCycle(farm_id=farm.id, plot_id=int(plot_id) if plot_id else None, crop=crop, variety=variety,
                         area_ha=area_ha, planting_date=planting, duration_days=duration,
                         expected_harvest_date=calc_harvest(planting, duration), status=status, notes=notes))
        db.commit()
    return RedirectResponse("/crops", status_code=303)


@app.get("/crops/{crop_id}/edit", response_class=HTMLResponse)
def edit_crop_page(crop_id: int, request: Request):
    with SessionLocal() as db:
        farm = current_farm(db)
        rows = db.query(CropCycle).order_by(CropCycle.id.desc()).all()
        plots = db.query(Plot).order_by(Plot.name).all()
        edit_crop = db.get(CropCycle, crop_id)
        if not edit_crop:
            return RedirectResponse("/crops", status_code=303)
        return templates.TemplateResponse(request, "crops.html", ctx(request, farm=farm, rows=rows, plots=plots, edit_crop=edit_crop))


@app.post("/crops/{crop_id}/edit")
def update_crop(crop_id: int, crop: str = Form(...), variety: str = Form(""), area_ha: float = Form(0),
                plot_id: str = Form(""), planting_date: str = Form(""), duration_days: str = Form(""),
                status: str = Form("Planned"), notes: str = Form("")):
    with SessionLocal() as db:
        row = db.get(CropCycle, crop_id)
        if row:
            planting = parse_date(planting_date)
            duration = to_int(duration_days, None) if duration_days != "" else None
            row.crop, row.variety, row.area_ha = crop, variety, area_ha
            row.plot_id = int(plot_id) if plot_id else None
            row.planting_date, row.duration_days = planting, duration
            row.expected_harvest_date = calc_harvest(planting, duration)
            row.status, row.notes = status, notes
            db.commit()
    return RedirectResponse("/crops", status_code=303)


@app.get("/crops/{crop_id}", response_class=HTMLResponse)
def crop_detail(crop_id: int, request: Request):
    with SessionLocal() as db:
        crop = db.get(CropCycle, crop_id)
        if not crop:
            return RedirectResponse("/crops", status_code=303)
        updates = db.query(CropWeeklyUpdate).filter(CropWeeklyUpdate.crop_cycle_id == crop_id).order_by(
            CropWeeklyUpdate.update_date.desc(), CropWeeklyUpdate.id.desc()).all()
        applications = db.query(CropApplication).filter(CropApplication.crop_cycle_id == crop_id).order_by(
            CropApplication.application_date.desc(), CropApplication.id.desc()).all()
        expenses = db.query(Expense).filter(Expense.crop_cycle_id == crop_id).order_by(Expense.expense_date.desc()).all()
        sales = db.query(Sale).filter(Sale.crop_cycle_id == crop_id).order_by(Sale.sale_date.desc()).all()
        total_expenses = sum(e.amount or 0 for e in expenses)
        total_sales = sum(s.total for s in sales)
        return templates.TemplateResponse(request, "crop_detail.html", ctx(request, crop=crop, updates=updates,
            applications=applications, expenses=expenses, sales=sales, total_expenses=total_expenses,
            total_sales=total_sales, margin=total_sales-total_expenses, edit_update=None, edit_application=None))


@app.post("/crops/{crop_id}/updates")
def add_crop_update(crop_id: int, update_date: str = Form(...), growth_stage: str = Form(""),
                    crop_condition: str = Form(""), pests_diseases: str = Form(""),
                    action_taken: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        db.add(CropWeeklyUpdate(crop_cycle_id=crop_id, update_date=parse_date(update_date) or date.today(),
                                growth_stage=growth_stage, crop_condition=crop_condition,
                                pests_diseases=pests_diseases, action_taken=action_taken, notes=notes))
        db.commit()
    return RedirectResponse(f"/crops/{crop_id}", status_code=303)


@app.get("/crops/{crop_id}/updates/{update_id}/edit", response_class=HTMLResponse)
def edit_crop_update_page(crop_id: int, update_id: int, request: Request):
    with SessionLocal() as db:
        crop = db.get(CropCycle, crop_id)
        if not crop:
            return RedirectResponse("/crops", status_code=303)
        updates = db.query(CropWeeklyUpdate).filter(CropWeeklyUpdate.crop_cycle_id == crop_id).order_by(CropWeeklyUpdate.update_date.desc()).all()
        applications = db.query(CropApplication).filter(CropApplication.crop_cycle_id == crop_id).order_by(CropApplication.application_date.desc()).all()
        expenses = db.query(Expense).filter(Expense.crop_cycle_id == crop_id).order_by(Expense.expense_date.desc()).all()
        sales = db.query(Sale).filter(Sale.crop_cycle_id == crop_id).order_by(Sale.sale_date.desc()).all()
        edit_update = db.get(CropWeeklyUpdate, update_id)
        return templates.TemplateResponse(request, "crop_detail.html", ctx(request, crop=crop, updates=updates,
            applications=applications, expenses=expenses, sales=sales, total_expenses=sum(e.amount for e in expenses),
            total_sales=sum(s.total for s in sales), margin=sum(s.total for s in sales)-sum(e.amount for e in expenses),
            edit_update=edit_update, edit_application=None))


@app.post("/crops/{crop_id}/updates/{update_id}/edit")
def update_crop_update(crop_id: int, update_id: int, update_date: str = Form(...), growth_stage: str = Form(""),
                       crop_condition: str = Form(""), pests_diseases: str = Form(""),
                       action_taken: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        row = db.get(CropWeeklyUpdate, update_id)
        if row and row.crop_cycle_id == crop_id:
            row.update_date = parse_date(update_date) or date.today()
            row.growth_stage, row.crop_condition = growth_stage, crop_condition
            row.pests_diseases, row.action_taken, row.notes = pests_diseases, action_taken, notes
            db.commit()
    return RedirectResponse(f"/crops/{crop_id}", status_code=303)


@app.post("/crops/{crop_id}/applications")
def add_crop_application(crop_id: int, application_date: str = Form(...), application_type: str = Form(...),
                         product: str = Form(...), quantity: str = Form(""), unit: str = Form(""), rate: str = Form(""),
                         method: str = Form(""), reason: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        db.add(CropApplication(crop_cycle_id=crop_id, application_date=parse_date(application_date) or date.today(),
                               application_type=application_type, product=product,
                               quantity=to_float(quantity, None) if quantity != "" else None,
                               unit=unit, rate=rate, method=method, reason=reason, notes=notes))
        db.commit()
    return RedirectResponse(f"/crops/{crop_id}", status_code=303)


@app.get("/crops/{crop_id}/applications/{application_id}/edit", response_class=HTMLResponse)
def edit_crop_application_page(crop_id: int, application_id: int, request: Request):
    with SessionLocal() as db:
        crop = db.get(CropCycle, crop_id)
        if not crop:
            return RedirectResponse("/crops", status_code=303)
        updates = db.query(CropWeeklyUpdate).filter(CropWeeklyUpdate.crop_cycle_id == crop_id).order_by(CropWeeklyUpdate.update_date.desc()).all()
        applications = db.query(CropApplication).filter(CropApplication.crop_cycle_id == crop_id).order_by(CropApplication.application_date.desc()).all()
        expenses = db.query(Expense).filter(Expense.crop_cycle_id == crop_id).order_by(Expense.expense_date.desc()).all()
        sales = db.query(Sale).filter(Sale.crop_cycle_id == crop_id).order_by(Sale.sale_date.desc()).all()
        edit_application = db.get(CropApplication, application_id)
        return templates.TemplateResponse(request, "crop_detail.html", ctx(request, crop=crop, updates=updates,
            applications=applications, expenses=expenses, sales=sales, total_expenses=sum(e.amount for e in expenses),
            total_sales=sum(s.total for s in sales), margin=sum(s.total for s in sales)-sum(e.amount for e in expenses),
            edit_update=None, edit_application=edit_application))


@app.post("/crops/{crop_id}/applications/{application_id}/edit")
def update_crop_application(crop_id: int, application_id: int, application_date: str = Form(...),
                            application_type: str = Form(...), product: str = Form(...), quantity: str = Form(""),
                            unit: str = Form(""), rate: str = Form(""), method: str = Form(""),
                            reason: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        row = db.get(CropApplication, application_id)
        if row and row.crop_cycle_id == crop_id:
            row.application_date = parse_date(application_date) or date.today()
            row.application_type, row.product = application_type, product
            row.quantity = to_float(quantity, None) if quantity != "" else None
            row.unit, row.rate, row.method, row.reason, row.notes = unit, rate, method, reason, notes
            db.commit()
    return RedirectResponse(f"/crops/{crop_id}", status_code=303)


# -------------------- Expenses --------------------
@app.get("/expenses", response_class=HTMLResponse)
def expenses(request: Request):
    with SessionLocal() as db:
        rows = db.query(Expense).order_by(Expense.expense_date.desc(), Expense.id.desc()).all()
        crops = db.query(CropCycle).order_by(CropCycle.crop).all()
        return templates.TemplateResponse(request, "expenses.html", ctx(request, rows=rows, crops=crops, edit_expense=None, error=""))


@app.post("/expenses")
async def add_expense(expense_date: str = Form(...), category: str = Form(...), amount: float = Form(...),
                      description: str = Form(...), crop_cycle_id: str = Form(""), supplier: str = Form(""),
                      payment_method: str = Form(""), reference: str = Form(""), notes: str = Form(""),
                      receipt: UploadFile | None = File(None)):
    try:
        receipt_filename, receipt_original_name = await save_receipt(receipt)
    except ValueError:
        return RedirectResponse("/expenses?upload_error=1", status_code=303)
    with SessionLocal() as db:
        farm = current_farm(db)
        db.add(Expense(farm_id=farm.id, crop_cycle_id=int(crop_cycle_id) if crop_cycle_id else None,
                       expense_date=parse_date(expense_date) or date.today(), category=category,
                       description=description, supplier=supplier, amount=amount, payment_method=payment_method,
                       reference=reference, receipt_filename=receipt_filename,
                       receipt_original_name=receipt_original_name, notes=notes))
        db.commit()
    return RedirectResponse("/expenses", status_code=303)


@app.get("/expenses/{expense_id}/edit", response_class=HTMLResponse)
def edit_expense_page(expense_id: int, request: Request):
    with SessionLocal() as db:
        rows = db.query(Expense).order_by(Expense.expense_date.desc(), Expense.id.desc()).all()
        crops = db.query(CropCycle).order_by(CropCycle.crop).all()
        edit_expense = db.get(Expense, expense_id)
        return templates.TemplateResponse(request, "expenses.html", ctx(request, rows=rows, crops=crops,
            edit_expense=edit_expense, error=""))


@app.post("/expenses/{expense_id}/edit")
async def update_expense(expense_id: int, expense_date: str = Form(...), category: str = Form(...),
                         amount: float = Form(...), description: str = Form(...), crop_cycle_id: str = Form(""),
                         supplier: str = Form(""), payment_method: str = Form(""), reference: str = Form(""),
                         notes: str = Form(""), receipt: UploadFile | None = File(None)):
    with SessionLocal() as db:
        row = db.get(Expense, expense_id)
        if row:
            row.expense_date = parse_date(expense_date) or date.today()
            row.category, row.amount, row.description = category, amount, description
            row.crop_cycle_id = int(crop_cycle_id) if crop_cycle_id else None
            row.supplier, row.payment_method, row.reference, row.notes = supplier, payment_method, reference, notes
            if receipt and receipt.filename:
                try:
                    filename, original = await save_receipt(receipt)
                    row.receipt_filename, row.receipt_original_name = filename, original
                except ValueError:
                    pass
            db.commit()
    return RedirectResponse("/expenses", status_code=303)


# -------------------- Sales --------------------
@app.get("/sales", response_class=HTMLResponse)
def sales(request: Request):
    with SessionLocal() as db:
        rows = db.query(Sale).order_by(Sale.sale_date.desc(), Sale.id.desc()).all()
        crops = db.query(CropCycle).order_by(CropCycle.crop).all()
        livestock_groups = db.query(Livestock).order_by(Livestock.species, Livestock.batch_name).all()
        animals = db.query(LivestockAnimal).order_by(LivestockAnimal.animal_code).all()
        stock = {c.id: produce_balance(db, c.id) for c in crops}
        return templates.TemplateResponse(request, "sales.html", ctx(request, rows=rows, crops=crops,
            livestock_groups=livestock_groups, animals=animals, stock=stock, edit_sale=None,
            stock_error=request.query_params.get("stock_error", ""), livestock_error=request.query_params.get("livestock_error", "")))


def _attach_sale_effect(db, sale: Sale):
    """Create the operational stock/herd effect for a financial sale."""
    if sale.sale_type == "Crop" and sale.crop_cycle_id:
        available = produce_balance(db, sale.crop_cycle_id)
        if sale.quantity > available + 1e-9:
            raise ValueError("stock")
        movement = ProduceMovement(farm_id=sale.farm_id, crop_cycle_id=sale.crop_cycle_id,
                                   movement_date=sale.sale_date, movement_type="Sale",
                                   quantity_change=-abs(sale.quantity), unit=sale.unit,
                                   reference=sale.reference or f"SALE-{sale.id}", notes=sale.notes)
        db.add(movement); db.flush(); sale.produce_movement_id = movement.id
    elif sale.sale_type == "Livestock" and sale.livestock_group_id:
        group = db.get(Livestock, sale.livestock_group_id)
        if not group or sale.quantity > group.current_total():
            raise ValueError("livestock")
        animal = db.get(LivestockAnimal, sale.livestock_animal_id) if sale.livestock_animal_id else None
        sex = animal.sex if animal else (sale.livestock_sex or "Unknown")
        qty = max(1, int(round(sale.quantity)))
        q_change, m_change, f_change, u_change = calculate_livestock_event_changes("Sale", qty, sex, 0, 0, 0)
        event = LivestockEvent(group_id=group.id, animal_id=animal.id if animal else None,
                               event_date=sale.sale_date, event_type="Sale", affected_sex=sex,
                               quantity=qty, quantity_change=q_change, male_change=m_change,
                               female_change=f_change, unknown_change=u_change,
                               outcome_status="Sold", notes=f"Financial sale #{sale.id}. {sale.notes or ''}".strip())
        db.add(event); db.flush(); sale.livestock_event_id = event.id
        sync_animal_from_events(db, animal)


def _detach_sale_effect(db, sale: Sale):
    if sale.produce_movement_id:
        row = db.get(ProduceMovement, sale.produce_movement_id)
        if row: db.delete(row)
        sale.produce_movement_id = None
    if sale.livestock_event_id:
        row = db.get(LivestockEvent, sale.livestock_event_id)
        old_animal = db.get(LivestockAnimal, row.animal_id) if row and row.animal_id else None
        if row: db.delete(row)
        db.flush()
        sync_animal_from_events(db, old_animal)
        sale.livestock_event_id = None


@app.post("/sales")
def add_sale(sale_date: str = Form(...), sale_type: str = Form("Crop"), buyer: str = Form(...),
             product: str = Form(""), quantity: float = Form(...), unit: str = Form("kg"),
             unit_price: float = Form(...), amount_paid: float = Form(0), crop_cycle_id: str = Form(""),
             livestock_group_id: str = Form(""), livestock_animal_id: str = Form(""), livestock_sex: str = Form("Unknown"),
             reference: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        farm = current_farm(db)
        crop_id = int(crop_cycle_id) if crop_cycle_id else None
        group_id = int(livestock_group_id) if livestock_group_id else None
        animal_id = int(livestock_animal_id) if livestock_animal_id else None
        if sale_type == "Crop" and crop_id and not product:
            c = db.get(CropCycle, crop_id); product = c.crop if c else "Crop"
        if sale_type == "Livestock" and group_id and not product:
            g = db.get(Livestock, group_id); product = g.species if g else "Livestock"
        sale = Sale(farm_id=farm.id, sale_type=sale_type, crop_cycle_id=crop_id if sale_type == "Crop" else None,
                    livestock_group_id=group_id if sale_type == "Livestock" else None,
                    livestock_animal_id=animal_id if sale_type == "Livestock" else None,
                    livestock_sex=livestock_sex if sale_type == "Livestock" else "Unknown",
                    sale_date=parse_date(sale_date) or date.today(), buyer=buyer, product=product or sale_type,
                    quantity=quantity, unit=unit, unit_price=unit_price, amount_paid=amount_paid,
                    reference=reference, notes=notes)
        db.add(sale); db.flush()
        try:
            _attach_sale_effect(db, sale)
        except ValueError as e:
            db.rollback()
            return RedirectResponse("/sales?stock_error=1" if str(e)=="stock" else "/sales?livestock_error=1", status_code=303)
        db.commit()
    return RedirectResponse("/sales", status_code=303)


@app.get("/sales/{sale_id}/edit", response_class=HTMLResponse)
def edit_sale_page(sale_id: int, request: Request):
    with SessionLocal() as db:
        rows = db.query(Sale).order_by(Sale.sale_date.desc(), Sale.id.desc()).all()
        crops = db.query(CropCycle).order_by(CropCycle.crop).all()
        livestock_groups = db.query(Livestock).order_by(Livestock.species, Livestock.batch_name).all()
        animals = db.query(LivestockAnimal).order_by(LivestockAnimal.animal_code).all()
        edit_sale = db.get(Sale, sale_id)
        stock = {c.id: produce_balance(db, c.id) for c in crops}
        if edit_sale and edit_sale.crop_cycle_id and edit_sale.produce_movement_id:
            stock[edit_sale.crop_cycle_id] = stock.get(edit_sale.crop_cycle_id, 0) + abs(edit_sale.quantity or 0)
        return templates.TemplateResponse(request, "sales.html", ctx(request, rows=rows, crops=crops,
            livestock_groups=livestock_groups, animals=animals, stock=stock, edit_sale=edit_sale,
            stock_error="", livestock_error=""))


@app.post("/sales/{sale_id}/edit")
def update_sale(sale_id: int, sale_date: str = Form(...), sale_type: str = Form("Crop"), buyer: str = Form(...),
                product: str = Form(""), quantity: float = Form(...), unit: str = Form("kg"),
                unit_price: float = Form(...), amount_paid: float = Form(0), crop_cycle_id: str = Form(""),
                livestock_group_id: str = Form(""), livestock_animal_id: str = Form(""), livestock_sex: str = Form("Unknown"),
                reference: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        row = db.get(Sale, sale_id)
        if row:
            _detach_sale_effect(db, row); db.flush()
            crop_id = int(crop_cycle_id) if crop_cycle_id else None
            group_id = int(livestock_group_id) if livestock_group_id else None
            animal_id = int(livestock_animal_id) if livestock_animal_id else None
            if sale_type == "Crop" and crop_id and not product:
                c = db.get(CropCycle, crop_id); product = c.crop if c else "Crop"
            if sale_type == "Livestock" and group_id and not product:
                g = db.get(Livestock, group_id); product = g.species if g else "Livestock"
            row.sale_type = sale_type
            row.crop_cycle_id = crop_id if sale_type == "Crop" else None
            row.livestock_group_id = group_id if sale_type == "Livestock" else None
            row.livestock_animal_id = animal_id if sale_type == "Livestock" else None
            row.livestock_sex = livestock_sex if sale_type == "Livestock" else "Unknown"
            row.sale_date, row.buyer, row.product = parse_date(sale_date) or date.today(), buyer, product or sale_type
            row.quantity, row.unit, row.unit_price, row.amount_paid = quantity, unit, unit_price, amount_paid
            row.reference, row.notes = reference, notes
            try:
                _attach_sale_effect(db, row)
            except ValueError as e:
                db.rollback()
                return RedirectResponse("/sales?stock_error=1" if str(e)=="stock" else "/sales?livestock_error=1", status_code=303)
            db.commit()
    return RedirectResponse("/sales", status_code=303)


# -------------------- Livestock --------------------
@app.get("/livestock", response_class=HTMLResponse)
def livestock(request: Request):
    with SessionLocal() as db:
        rows = db.query(Livestock).order_by(Livestock.batch_name).all()
        return templates.TemplateResponse(request, "livestock.html", ctx(request, rows=rows, edit_group=None))


@app.post("/livestock")
def add_livestock(batch_name: str = Form(...), species: str = Form(...), breed: str = Form(""),
                  male_count: int = Form(0), female_count: int = Form(0), unknown_count: int = Form(0),
                  acquisition_date: str = Form(""), status: str = Form("Active"), notes: str = Form("")):
    total = max(0, male_count) + max(0, female_count) + max(0, unknown_count)
    with SessionLocal() as db:
        farm = current_farm(db)
        db.add(Livestock(farm_id=farm.id, batch_name=batch_name, species=species, breed=breed,
                         quantity=total, male_count=max(0, male_count), female_count=max(0, female_count),
                         unknown_count=max(0, unknown_count), acquisition_date=parse_date(acquisition_date),
                         status=status, notes=notes))
        db.commit()
    return RedirectResponse("/livestock", status_code=303)


@app.get("/livestock/{group_id}/edit", response_class=HTMLResponse)
def edit_livestock_page(group_id: int, request: Request):
    with SessionLocal() as db:
        rows = db.query(Livestock).order_by(Livestock.batch_name).all()
        edit_group = db.get(Livestock, group_id)
        return templates.TemplateResponse(request, "livestock.html", ctx(request, rows=rows, edit_group=edit_group))


@app.post("/livestock/{group_id}/edit")
def update_livestock(group_id: int, batch_name: str = Form(...), species: str = Form(...), breed: str = Form(""),
                     male_count: int = Form(0), female_count: int = Form(0), unknown_count: int = Form(0),
                     acquisition_date: str = Form(""), status: str = Form("Active"), notes: str = Form("")):
    with SessionLocal() as db:
        row = db.get(Livestock, group_id)
        if row:
            total = max(0, male_count) + max(0, female_count) + max(0, unknown_count)
            row.batch_name, row.species, row.breed = batch_name, species, breed
            row.quantity = total
            row.male_count, row.female_count, row.unknown_count = max(0, male_count), max(0, female_count), max(0, unknown_count)
            row.acquisition_date, row.status, row.notes = parse_date(acquisition_date), status, notes
            db.commit()
    return RedirectResponse("/livestock", status_code=303)


@app.get("/livestock/{group_id}", response_class=HTMLResponse)
def livestock_detail(group_id: int, request: Request):
    with SessionLocal() as db:
        group = db.get(Livestock, group_id)
        if not group:
            return RedirectResponse("/livestock", status_code=303)
        animals = db.query(LivestockAnimal).filter(LivestockAnimal.group_id == group_id).order_by(LivestockAnimal.animal_code).all()
        events = db.query(LivestockEvent).filter(LivestockEvent.group_id == group_id).order_by(
            LivestockEvent.event_date.desc(), LivestockEvent.id.desc()).all()
        return templates.TemplateResponse(request, "livestock_detail.html", ctx(request, group=group, animals=animals,
            events=events, edit_animal=None, edit_event=None, today=date.today()))


@app.post("/livestock/{group_id}/animals")
def add_livestock_animal(group_id: int, animal_code: str = Form(...), sex: str = Form("Unknown"),
                         birth_or_acquired_date: str = Form(""), status: str = Form("Active"),
                         pregnancy_status: str = Form("Not Pregnant"), dam: str = Form(""), sire: str = Form(""),
                         notes: str = Form("")):
    with SessionLocal() as db:
        db.add(LivestockAnimal(group_id=group_id, animal_code=animal_code, sex=sex,
                               birth_or_acquired_date=parse_date(birth_or_acquired_date), status=status,
                               pregnancy_status=pregnancy_status, dam=dam, sire=sire, notes=notes))
        db.commit()
    return RedirectResponse(f"/livestock/{group_id}", status_code=303)


@app.get("/livestock/{group_id}/animals/{animal_id}/edit", response_class=HTMLResponse)
def edit_livestock_animal_page(group_id: int, animal_id: int, request: Request):
    with SessionLocal() as db:
        group = db.get(Livestock, group_id)
        animals = db.query(LivestockAnimal).filter(LivestockAnimal.group_id == group_id).order_by(LivestockAnimal.animal_code).all()
        events = db.query(LivestockEvent).filter(LivestockEvent.group_id == group_id).order_by(LivestockEvent.event_date.desc()).all()
        edit_animal = db.get(LivestockAnimal, animal_id)
        return templates.TemplateResponse(request, "livestock_detail.html", ctx(request, group=group, animals=animals,
            events=events, edit_animal=edit_animal, edit_event=None, today=date.today()))


@app.post("/livestock/{group_id}/animals/{animal_id}/edit")
def update_livestock_animal(group_id: int, animal_id: int, animal_code: str = Form(...), sex: str = Form("Unknown"),
                            birth_or_acquired_date: str = Form(""), status: str = Form("Active"),
                            pregnancy_status: str = Form("Not Pregnant"), dam: str = Form(""), sire: str = Form(""),
                            notes: str = Form("")):
    with SessionLocal() as db:
        row = db.get(LivestockAnimal, animal_id)
        if row and row.group_id == group_id:
            row.animal_code, row.sex = animal_code, sex
            row.birth_or_acquired_date, row.status = parse_date(birth_or_acquired_date), status
            row.pregnancy_status, row.dam, row.sire, row.notes = pregnancy_status, dam, sire, notes
            db.commit()
    return RedirectResponse(f"/livestock/{group_id}", status_code=303)


@app.post("/livestock/{group_id}/events")
def add_livestock_event(group_id: int, event_date: str = Form(...), event_type: str = Form(...), animal_id: str = Form(""),
                        affected_sex: str = Form("Unknown"), quantity: int = Form(0),
                        product_or_medicine: str = Form(""), symptoms_diagnosis: str = Form(""), dosage: str = Form(""),
                        provider: str = Form(""), next_due_date: str = Form(""), mating_date: str = Form(""),
                        gestation_days: str = Form(""), expected_birth_date: str = Form(""),
                        birth_male: int = Form(0), birth_female: int = Form(0), birth_unknown: int = Form(0),
                        cause_of_death: str = Form(""), outcome_status: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        animal = db.get(LivestockAnimal, int(animal_id)) if animal_id else None
        if animal and animal.group_id != group_id:
            animal = None
        sex = animal.sex if animal and affected_sex == "Unknown" else affected_sex
        gest = to_int(gestation_days, None) if gestation_days != "" else None
        mdate = parse_date(mating_date)
        exp_birth = parse_date(expected_birth_date)
        if not exp_birth and mdate and gest is not None:
            exp_birth = mdate + timedelta(days=gest)
        q_change, m_change, f_change, u_change = calculate_livestock_event_changes(
            event_type, quantity, sex, birth_male, birth_female, birth_unknown)
        event = LivestockEvent(group_id=group_id, animal_id=animal.id if animal else None,
                               event_date=parse_date(event_date) or date.today(), event_type=event_type,
                               affected_sex=sex, quantity=max(0, quantity), quantity_change=q_change,
                               male_change=m_change, female_change=f_change, unknown_change=u_change,
                               product_or_medicine=product_or_medicine, symptoms_diagnosis=symptoms_diagnosis,
                               dosage=dosage, provider=provider, next_due_date=parse_date(next_due_date),
                               mating_date=mdate, gestation_days=gest, expected_birth_date=exp_birth,
                               birth_male=max(0, birth_male), birth_female=max(0, birth_female),
                               birth_unknown=max(0, birth_unknown), cause_of_death=cause_of_death,
                               outcome_status=outcome_status, notes=notes)
        db.add(event)
        db.flush()
        sync_animal_from_events(db, animal)
        db.commit()
    return RedirectResponse(f"/livestock/{group_id}", status_code=303)


@app.get("/livestock/{group_id}/events/{event_id}/edit", response_class=HTMLResponse)
def edit_livestock_event_page(group_id: int, event_id: int, request: Request):
    with SessionLocal() as db:
        group = db.get(Livestock, group_id)
        animals = db.query(LivestockAnimal).filter(LivestockAnimal.group_id == group_id).order_by(LivestockAnimal.animal_code).all()
        events = db.query(LivestockEvent).filter(LivestockEvent.group_id == group_id).order_by(LivestockEvent.event_date.desc()).all()
        edit_event = db.get(LivestockEvent, event_id)
        return templates.TemplateResponse(request, "livestock_detail.html", ctx(request, group=group, animals=animals,
            events=events, edit_animal=None, edit_event=edit_event, today=date.today()))


@app.post("/livestock/{group_id}/events/{event_id}/edit")
def update_livestock_event(group_id: int, event_id: int, event_date: str = Form(...), event_type: str = Form(...),
                           animal_id: str = Form(""), affected_sex: str = Form("Unknown"), quantity: int = Form(0),
                           product_or_medicine: str = Form(""), symptoms_diagnosis: str = Form(""), dosage: str = Form(""),
                           provider: str = Form(""), next_due_date: str = Form(""), mating_date: str = Form(""),
                           gestation_days: str = Form(""), expected_birth_date: str = Form(""),
                           birth_male: int = Form(0), birth_female: int = Form(0), birth_unknown: int = Form(0),
                           cause_of_death: str = Form(""), outcome_status: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        row = db.get(LivestockEvent, event_id)
        if row and row.group_id == group_id:
            old_animal = db.get(LivestockAnimal, row.animal_id) if row.animal_id else None
            animal = db.get(LivestockAnimal, int(animal_id)) if animal_id else None
            if animal and animal.group_id != group_id:
                animal = None
            sex = animal.sex if animal and affected_sex == "Unknown" else affected_sex
            gest = to_int(gestation_days, None) if gestation_days != "" else None
            mdate = parse_date(mating_date)
            exp_birth = parse_date(expected_birth_date)
            if not exp_birth and mdate and gest is not None:
                exp_birth = mdate + timedelta(days=gest)
            q_change, m_change, f_change, u_change = calculate_livestock_event_changes(
                event_type, quantity, sex, birth_male, birth_female, birth_unknown)
            row.animal_id = animal.id if animal else None
            row.event_date, row.event_type, row.affected_sex = parse_date(event_date) or date.today(), event_type, sex
            row.quantity, row.quantity_change = max(0, quantity), q_change
            row.male_change, row.female_change, row.unknown_change = m_change, f_change, u_change
            row.product_or_medicine, row.symptoms_diagnosis, row.dosage = product_or_medicine, symptoms_diagnosis, dosage
            row.provider, row.next_due_date = provider, parse_date(next_due_date)
            row.mating_date, row.gestation_days, row.expected_birth_date = mdate, gest, exp_birth
            row.birth_male, row.birth_female, row.birth_unknown = max(0, birth_male), max(0, birth_female), max(0, birth_unknown)
            row.cause_of_death, row.outcome_status, row.notes = cause_of_death, outcome_status, notes
            sync_animal_from_events(db, old_animal)
            if animal and (not old_animal or old_animal.id != animal.id):
                sync_animal_from_events(db, animal)
            db.commit()
    return RedirectResponse(f"/livestock/{group_id}", status_code=303)


# -------------------- Water --------------------
@app.get("/water", response_class=HTMLResponse)
def water(request: Request):
    with SessionLocal() as db:
        rows = db.query(WaterSource).order_by(WaterSource.name).all()
        return templates.TemplateResponse(request, "water.html", ctx(request, rows=rows, edit_water=None))


@app.post("/water")
def add_water(name: str = Form(...), source_type: str = Form(...), yield_m3_hr: str = Form(""),
              storage_litres: str = Form(""), status: str = Form("Active"), notes: str = Form("")):
    with SessionLocal() as db:
        farm = current_farm(db)
        db.add(WaterSource(farm_id=farm.id, name=name, source_type=source_type,
                           yield_m3_hr=float(yield_m3_hr) if yield_m3_hr else None,
                           storage_litres=float(storage_litres) if storage_litres else None,
                           status=status, notes=notes))
        db.commit()
    return RedirectResponse("/water", status_code=303)


@app.get("/water/{water_id}/edit", response_class=HTMLResponse)
def edit_water_page(water_id: int, request: Request):
    with SessionLocal() as db:
        rows = db.query(WaterSource).order_by(WaterSource.name).all()
        edit_water = db.get(WaterSource, water_id)
        return templates.TemplateResponse(request, "water.html", ctx(request, rows=rows, edit_water=edit_water))


@app.post("/water/{water_id}/edit")
def update_water(water_id: int, name: str = Form(...), source_type: str = Form(...), yield_m3_hr: str = Form(""),
                 storage_litres: str = Form(""), status: str = Form("Active"), notes: str = Form("")):
    with SessionLocal() as db:
        row = db.get(WaterSource, water_id)
        if row:
            row.name, row.source_type = name, source_type
            row.yield_m3_hr = float(yield_m3_hr) if yield_m3_hr else None
            row.storage_litres = float(storage_litres) if storage_litres else None
            row.status, row.notes = status, notes
            db.commit()
    return RedirectResponse("/water", status_code=303)



# -------------------- Harvest & Produce Inventory --------------------
@app.get("/harvests", response_class=HTMLResponse)
def harvests(request: Request):
    with SessionLocal() as db:
        rows = db.query(Harvest).order_by(Harvest.harvest_date.desc(), Harvest.id.desc()).all()
        crops = db.query(CropCycle).order_by(CropCycle.crop).all()
        movements = db.query(ProduceMovement).filter(ProduceMovement.movement_type != "Harvest").order_by(
            ProduceMovement.movement_date.desc(), ProduceMovement.id.desc()).limit(100).all()
        stock = {c.id: produce_balance(db, c.id) for c in crops}
        return templates.TemplateResponse(request, "harvests.html", ctx(request, rows=rows, crops=crops,
            movements=movements, stock=stock, edit_harvest=None, error=request.query_params.get("error", "")))


@app.post("/harvests")
def add_harvest(crop_cycle_id: int = Form(...), harvest_date: str = Form(...), quantity_harvested: float = Form(...),
                unit: str = Form("kg"), grade: str = Form(""), reject_qty: float = Form(0), notes: str = Form("")):
    marketable = max(0.0, quantity_harvested - max(0.0, reject_qty))
    with SessionLocal() as db:
        farm = current_farm(db)
        row = Harvest(farm_id=farm.id, crop_cycle_id=crop_cycle_id,
                      harvest_date=parse_date(harvest_date) or date.today(), quantity_harvested=quantity_harvested,
                      unit=unit, grade=grade, reject_qty=max(0, reject_qty), marketable_qty=marketable, notes=notes)
        db.add(row); db.flush()
        db.add(ProduceMovement(farm_id=farm.id, crop_cycle_id=crop_cycle_id, harvest_id=row.id,
                               movement_date=row.harvest_date, movement_type="Harvest", quantity_change=marketable,
                               unit=unit, reference=f"HARVEST-{row.id}", notes=notes))
        db.commit()
    return RedirectResponse("/harvests", status_code=303)


@app.get("/harvests/{harvest_id}/edit", response_class=HTMLResponse)
def edit_harvest_page(harvest_id: int, request: Request):
    with SessionLocal() as db:
        rows = db.query(Harvest).order_by(Harvest.harvest_date.desc(), Harvest.id.desc()).all()
        crops = db.query(CropCycle).order_by(CropCycle.crop).all()
        movements = db.query(ProduceMovement).filter(ProduceMovement.movement_type != "Harvest").order_by(
            ProduceMovement.movement_date.desc(), ProduceMovement.id.desc()).limit(100).all()
        stock = {c.id: produce_balance(db, c.id) for c in crops}
        edit_harvest = db.get(Harvest, harvest_id)
        return templates.TemplateResponse(request, "harvests.html", ctx(request, rows=rows, crops=crops,
            movements=movements, stock=stock, edit_harvest=edit_harvest, error=""))


@app.post("/harvests/{harvest_id}/edit")
def update_harvest(harvest_id: int, crop_cycle_id: int = Form(...), harvest_date: str = Form(...),
                   quantity_harvested: float = Form(...), unit: str = Form("kg"), grade: str = Form(""),
                   reject_qty: float = Form(0), notes: str = Form("")):
    with SessionLocal() as db:
        row = db.get(Harvest, harvest_id)
        if row:
            movement = db.query(ProduceMovement).filter(ProduceMovement.harvest_id == row.id,
                                                        ProduceMovement.movement_type == "Harvest").first()
            old_available_without_this = produce_balance(db, row.crop_cycle_id) - (movement.quantity_change if movement else 0)
            marketable = max(0.0, quantity_harvested - max(0.0, reject_qty))
            # Do not allow an edit that would leave already-used stock negative.
            if row.crop_cycle_id == crop_cycle_id and old_available_without_this + marketable < -1e-9:
                return RedirectResponse("/harvests?error=negative", status_code=303)
            row.crop_cycle_id, row.harvest_date = crop_cycle_id, parse_date(harvest_date) or date.today()
            row.quantity_harvested, row.unit, row.grade = quantity_harvested, unit, grade
            row.reject_qty, row.marketable_qty, row.notes = max(0, reject_qty), marketable, notes
            if movement:
                movement.crop_cycle_id, movement.movement_date = crop_cycle_id, row.harvest_date
                movement.quantity_change, movement.unit, movement.notes = marketable, unit, notes
            else:
                farm = current_farm(db)
                db.add(ProduceMovement(farm_id=farm.id, crop_cycle_id=crop_cycle_id, harvest_id=row.id,
                                       movement_date=row.harvest_date, movement_type="Harvest",
                                       quantity_change=marketable, unit=unit, reference=f"HARVEST-{row.id}", notes=notes))
            db.commit()
    return RedirectResponse("/harvests", status_code=303)


@app.post("/produce-adjustments")
def add_produce_adjustment(crop_cycle_id: int = Form(...), movement_date: str = Form(...),
                           movement_type: str = Form(...), quantity: float = Form(...), unit: str = Form("kg"),
                           reference: str = Form(""), notes: str = Form("")):
    negative_types = {"Spoilage/Loss", "Own Use", "Donation", "Processing", "Transfer Out"}
    change = -abs(quantity) if movement_type in negative_types else abs(quantity)
    with SessionLocal() as db:
        if change < 0 and abs(change) > produce_balance(db, crop_cycle_id) + 1e-9:
            return RedirectResponse("/harvests?error=stock", status_code=303)
        farm = current_farm(db)
        db.add(ProduceMovement(farm_id=farm.id, crop_cycle_id=crop_cycle_id,
                               movement_date=parse_date(movement_date) or date.today(), movement_type=movement_type,
                               quantity_change=change, unit=unit, reference=reference, notes=notes))
        db.commit()
    return RedirectResponse("/harvests", status_code=303)


# -------------------- Farm Inventory --------------------
@app.get("/inventory", response_class=HTMLResponse)
def inventory(request: Request):
    with SessionLocal() as db:
        rows = db.query(InventoryItem).order_by(InventoryItem.category, InventoryItem.item_name).all()
        low_stock = [x for x in rows if x.min_stock_level is not None and x.current_qty() <= x.min_stock_level]
        return templates.TemplateResponse(request, "inventory.html", ctx(request, rows=rows, low_stock=low_stock, edit_item=None))


@app.post("/inventory")
def add_inventory_item(item_name: str = Form(...), category: str = Form(""), item_type: str = Form("Durable"),
                       opening_qty: float = Form(0), unit: str = Form("unit"), location: str = Form(""),
                       condition: str = Form("Good"), acquisition_date: str = Form(""), unit_cost: str = Form(""),
                       min_stock_level: str = Form(""), supplier: str = Form(""), reference: str = Form(""),
                       last_service_date: str = Form(""), next_service_date: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        farm = current_farm(db)
        db.add(InventoryItem(farm_id=farm.id, item_name=item_name, category=category, item_type=item_type,
                             opening_qty=max(0, opening_qty), unit=unit, location=location, condition=condition,
                             acquisition_date=parse_date(acquisition_date), unit_cost=to_float(unit_cost, None) if unit_cost else None,
                             min_stock_level=to_float(min_stock_level, None) if min_stock_level else None,
                             supplier=supplier, reference=reference, last_service_date=parse_date(last_service_date),
                             next_service_date=parse_date(next_service_date), notes=notes))
        db.commit()
    return RedirectResponse("/inventory", status_code=303)


@app.get("/inventory/{item_id}/edit", response_class=HTMLResponse)
def edit_inventory_page(item_id: int, request: Request):
    with SessionLocal() as db:
        rows = db.query(InventoryItem).order_by(InventoryItem.category, InventoryItem.item_name).all()
        low_stock = [x for x in rows if x.min_stock_level is not None and x.current_qty() <= x.min_stock_level]
        return templates.TemplateResponse(request, "inventory.html", ctx(request, rows=rows, low_stock=low_stock,
            edit_item=db.get(InventoryItem, item_id)))


@app.post("/inventory/{item_id}/edit")
def update_inventory_item(item_id: int, item_name: str = Form(...), category: str = Form(""),
                          item_type: str = Form("Durable"), opening_qty: float = Form(0), unit: str = Form("unit"),
                          location: str = Form(""), condition: str = Form("Good"), acquisition_date: str = Form(""),
                          unit_cost: str = Form(""), min_stock_level: str = Form(""), supplier: str = Form(""),
                          reference: str = Form(""), last_service_date: str = Form(""), next_service_date: str = Form(""),
                          notes: str = Form("")):
    with SessionLocal() as db:
        row = db.get(InventoryItem, item_id)
        if row:
            row.item_name, row.category, row.item_type = item_name, category, item_type
            row.opening_qty, row.unit, row.location, row.condition = max(0, opening_qty), unit, location, condition
            row.acquisition_date = parse_date(acquisition_date)
            row.unit_cost = to_float(unit_cost, None) if unit_cost else None
            row.min_stock_level = to_float(min_stock_level, None) if min_stock_level else None
            row.supplier, row.reference = supplier, reference
            row.last_service_date, row.next_service_date = parse_date(last_service_date), parse_date(next_service_date)
            row.notes = notes; db.commit()
    return RedirectResponse("/inventory", status_code=303)


@app.get("/inventory/{item_id}", response_class=HTMLResponse)
def inventory_detail(item_id: int, request: Request):
    with SessionLocal() as db:
        item = db.get(InventoryItem, item_id)
        if not item: return RedirectResponse("/inventory", status_code=303)
        txns = db.query(InventoryTransaction).filter(InventoryTransaction.item_id == item_id).order_by(
            InventoryTransaction.transaction_date.desc(), InventoryTransaction.id.desc()).all()
        return templates.TemplateResponse(request, "inventory_detail.html", ctx(request, item=item, txns=txns, edit_txn=None))


@app.post("/inventory/{item_id}/transactions")
def add_inventory_txn(item_id: int, transaction_date: str = Form(...), transaction_type: str = Form(...),
                      quantity: float = Form(...), issued_to: str = Form(""), from_to_location: str = Form(""),
                      reference: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        item = db.get(InventoryItem, item_id)
        if item:
            change = inventory_change(transaction_type, quantity, item.item_type)
            if change < 0 and abs(change) > item.current_qty() + 1e-9:
                return RedirectResponse(f"/inventory/{item_id}?error=stock", status_code=303)
            db.add(InventoryTransaction(item_id=item_id, transaction_date=parse_date(transaction_date) or date.today(),
                                        transaction_type=transaction_type, quantity=abs(quantity), quantity_change=change,
                                        issued_to=issued_to, from_to_location=from_to_location,
                                        reference=reference, notes=notes))
            db.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)


@app.get("/inventory/{item_id}/transactions/{txn_id}/edit", response_class=HTMLResponse)
def edit_inventory_txn_page(item_id: int, txn_id: int, request: Request):
    with SessionLocal() as db:
        item = db.get(InventoryItem, item_id)
        if not item: return RedirectResponse("/inventory", status_code=303)
        txns = db.query(InventoryTransaction).filter(InventoryTransaction.item_id == item_id).order_by(
            InventoryTransaction.transaction_date.desc(), InventoryTransaction.id.desc()).all()
        return templates.TemplateResponse(request, "inventory_detail.html", ctx(request, item=item, txns=txns,
            edit_txn=db.get(InventoryTransaction, txn_id)))


@app.post("/inventory/{item_id}/transactions/{txn_id}/edit")
def update_inventory_txn(item_id: int, txn_id: int, transaction_date: str = Form(...),
                         transaction_type: str = Form(...), quantity: float = Form(...), issued_to: str = Form(""),
                         from_to_location: str = Form(""), reference: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        item = db.get(InventoryItem, item_id); row = db.get(InventoryTransaction, txn_id)
        if item and row and row.item_id == item_id:
            balance_without = item.current_qty() - row.quantity_change
            change = inventory_change(transaction_type, quantity, item.item_type)
            if balance_without + change < -1e-9:
                return RedirectResponse(f"/inventory/{item_id}?error=stock", status_code=303)
            row.transaction_date = parse_date(transaction_date) or date.today()
            row.transaction_type, row.quantity, row.quantity_change = transaction_type, abs(quantity), change
            row.issued_to, row.from_to_location, row.reference, row.notes = issued_to, from_to_location, reference, notes
            db.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)


# -------------------- Incidents, Knowledge & Help --------------------
@app.get("/incidents", response_class=HTMLResponse)
def incidents(request: Request):
    with SessionLocal() as db:
        rows = db.query(Incident).order_by(Incident.incident_date.desc(), Incident.id.desc()).all()
        plots = db.query(Plot).order_by(Plot.name).all(); crops = db.query(CropCycle).order_by(CropCycle.crop).all()
        groups = db.query(Livestock).order_by(Livestock.batch_name).all(); animals = db.query(LivestockAnimal).order_by(LivestockAnimal.animal_code).all()
        waters = db.query(WaterSource).order_by(WaterSource.name).all()
        knowledge = db.query(KnowledgeArticle).filter(KnowledgeArticle.active == 1).order_by(KnowledgeArticle.id.desc()).all()
        return templates.TemplateResponse(request, "incidents.html", ctx(request, rows=rows, plots=plots, crops=crops,
            groups=groups, animals=animals, waters=waters, knowledge=knowledge, edit_incident=None))


def _sync_incident_knowledge(db, incident: Incident):
    article = db.query(KnowledgeArticle).filter(KnowledgeArticle.source_incident_id == incident.id).first()
    should_exist = incident.status == "Resolved" and bool((incident.lessons_learned or "").strip())
    if should_exist:
        if not article:
            article = KnowledgeArticle(farm_id=incident.farm_id, source_incident_id=incident.id,
                                       title=f"Incident lesson: {incident.title}", category=incident.category,
                                       body=incident.lessons_learned, tags=incident.category, active=1)
            db.add(article)
        else:
            article.title, article.category, article.body, article.active = f"Incident lesson: {incident.title}", incident.category, incident.lessons_learned, 1
    elif article:
        article.active = 0


@app.post("/incidents")
async def add_incident(incident_date: str = Form(...), category: str = Form(...), severity: str = Form("Medium"),
                       title: str = Form(...), description: str = Form(...), plot_id: str = Form(""),
                       crop_cycle_id: str = Form(""), livestock_group_id: str = Form(""), livestock_animal_id: str = Form(""),
                       water_source_id: str = Form(""), immediate_action: str = Form(""), follow_up_action: str = Form(""),
                       responsible_person: str = Form(""), status: str = Form("Open"), resolution_date: str = Form(""),
                       cost_loss_estimate: float = Form(0), lessons_learned: str = Form(""), notes: str = Form(""),
                       attachment: UploadFile | None = File(None)):
    fn, original = await save_incident_attachment(attachment)
    with SessionLocal() as db:
        farm = current_farm(db)
        row = Incident(farm_id=farm.id, incident_date=parse_date(incident_date) or date.today(), category=category,
                       severity=severity, title=title, description=description, plot_id=int(plot_id) if plot_id else None,
                       crop_cycle_id=int(crop_cycle_id) if crop_cycle_id else None,
                       livestock_group_id=int(livestock_group_id) if livestock_group_id else None,
                       livestock_animal_id=int(livestock_animal_id) if livestock_animal_id else None,
                       water_source_id=int(water_source_id) if water_source_id else None,
                       immediate_action=immediate_action, follow_up_action=follow_up_action,
                       responsible_person=responsible_person, status=status, resolution_date=parse_date(resolution_date),
                       cost_loss_estimate=max(0, cost_loss_estimate), lessons_learned=lessons_learned,
                       attachment_filename=fn, attachment_original_name=original, notes=notes)
        db.add(row); db.flush(); _sync_incident_knowledge(db, row); db.commit()
    return RedirectResponse("/incidents", status_code=303)


@app.get("/incidents/{incident_id}/edit", response_class=HTMLResponse)
def edit_incident_page(incident_id: int, request: Request):
    with SessionLocal() as db:
        rows = db.query(Incident).order_by(Incident.incident_date.desc(), Incident.id.desc()).all()
        plots = db.query(Plot).order_by(Plot.name).all(); crops = db.query(CropCycle).order_by(CropCycle.crop).all()
        groups = db.query(Livestock).order_by(Livestock.batch_name).all(); animals = db.query(LivestockAnimal).order_by(LivestockAnimal.animal_code).all()
        waters = db.query(WaterSource).order_by(WaterSource.name).all(); knowledge = db.query(KnowledgeArticle).filter(KnowledgeArticle.active == 1).all()
        return templates.TemplateResponse(request, "incidents.html", ctx(request, rows=rows, plots=plots, crops=crops,
            groups=groups, animals=animals, waters=waters, knowledge=knowledge, edit_incident=db.get(Incident, incident_id)))


@app.post("/incidents/{incident_id}/edit")
async def update_incident(incident_id: int, incident_date: str = Form(...), category: str = Form(...),
                          severity: str = Form("Medium"), title: str = Form(...), description: str = Form(...),
                          plot_id: str = Form(""), crop_cycle_id: str = Form(""), livestock_group_id: str = Form(""),
                          livestock_animal_id: str = Form(""), water_source_id: str = Form(""), immediate_action: str = Form(""),
                          follow_up_action: str = Form(""), responsible_person: str = Form(""), status: str = Form("Open"),
                          resolution_date: str = Form(""), cost_loss_estimate: float = Form(0), lessons_learned: str = Form(""),
                          notes: str = Form(""), attachment: UploadFile | None = File(None)):
    with SessionLocal() as db:
        row = db.get(Incident, incident_id)
        if row:
            row.incident_date, row.category, row.severity = parse_date(incident_date) or date.today(), category, severity
            row.title, row.description = title, description
            row.plot_id = int(plot_id) if plot_id else None; row.crop_cycle_id = int(crop_cycle_id) if crop_cycle_id else None
            row.livestock_group_id = int(livestock_group_id) if livestock_group_id else None
            row.livestock_animal_id = int(livestock_animal_id) if livestock_animal_id else None
            row.water_source_id = int(water_source_id) if water_source_id else None
            row.immediate_action, row.follow_up_action, row.responsible_person = immediate_action, follow_up_action, responsible_person
            row.status, row.resolution_date = status, parse_date(resolution_date)
            row.cost_loss_estimate, row.lessons_learned, row.notes = max(0, cost_loss_estimate), lessons_learned, notes
            if attachment and attachment.filename:
                fn, original = await save_incident_attachment(attachment); row.attachment_filename, row.attachment_original_name = fn, original
            _sync_incident_knowledge(db, row); db.commit()
    return RedirectResponse("/incidents", status_code=303)


@app.post("/knowledge")
def add_knowledge(title: str = Form(...), category: str = Form("General"), body: str = Form(...), tags: str = Form("")):
    with SessionLocal() as db:
        farm = current_farm(db); db.add(KnowledgeArticle(farm_id=farm.id, title=title, category=category, body=body, tags=tags, active=1)); db.commit()
    return RedirectResponse("/incidents#knowledge", status_code=303)


def offline_help_answer(db, query: str):
    q = (query or "").strip().lower(); words = [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in q.replace("?", " ").replace(",", " ").split() if len(w) > 2]
    if not q: return "Ask about stock, livestock health dates, inventory, incidents or lessons learned.", []
    sources = []
    if any(x in q for x in ["vaccin", "deworm", "health due", "due"]):
        due = db.query(LivestockEvent).filter(LivestockEvent.next_due_date.is_not(None), LivestockEvent.next_due_date >= date.today()).order_by(LivestockEvent.next_due_date).limit(8).all()
        if due:
            text = "Upcoming livestock health dates: " + "; ".join(f"{e.next_due_date}: {e.group.batch_name} - {e.event_type} ({e.product_or_medicine or 'no product recorded'})" for e in due)
            return text, ["Livestock health records"]
    if any(x in q for x in ["harvest", "produce", "stock", "available"]):
        crops = db.query(CropCycle).order_by(CropCycle.crop).all(); bits=[]
        for c in crops:
            bal=produce_balance(db,c.id)
            if bal: bits.append(f"{c.label}: {bal:g}")
        if bits: return "Current produce balances: " + "; ".join(bits), ["Harvest & produce inventory"]
    inv = db.query(InventoryItem).all()
    matches=[i for i in inv if any(w in (i.item_name+' '+i.category).lower() for w in words)]
    if matches:
        return "Farm inventory matches: " + "; ".join(f"{i.item_name}: {i.current_qty():g} {i.unit} ({i.condition})" for i in matches[:8]), ["Farm inventory"]
    incidents = db.query(Incident).order_by(Incident.incident_date.desc()).all()
    km = db.query(KnowledgeArticle).filter(KnowledgeArticle.active == 1).all()
    scored=[]
    for kind,obj,textv in [("Incident",x,(x.title+' '+x.description+' '+(x.lessons_learned or '')+' '+x.category).lower()) for x in incidents] + [("Knowledge",x,(x.title+' '+x.body+' '+x.tags+' '+x.category).lower()) for x in km]:
        score=sum(1 for w in words if w in textv)
        if score: scored.append((score,kind,obj))
    scored.sort(key=lambda x:x[0], reverse=True)
    if scored:
        lines=[]
        for _,kind,obj in scored[:5]:
            if kind=="Incident": lines.append(f"{obj.incident_date} {obj.title}: {obj.lessons_learned or obj.immediate_action or obj.description}")
            else: lines.append(f"{obj.title}: {obj.body}")
        return "From FiHay's local knowledge: " + " | ".join(lines), [x[1] for x in scored[:5]]
    return "I could not find a matching FiHay record yet. Record the issue as an Incident or add an article to the Knowledge Base, then this offline assistant can retrieve it later.", []


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return templates.TemplateResponse(request, "help.html", ctx(request, query="", answer="", sources=[]))


@app.post("/help", response_class=HTMLResponse)
def help_query(request: Request, query: str = Form(...)):
    with SessionLocal() as db:
        answer, sources = offline_help_answer(db, query)
        return templates.TemplateResponse(request, "help.html", ctx(request, query=query, answer=answer, sources=sources))



# -------------------- Farm users / permissions --------------------
@app.get("/admin/users", response_class=HTMLResponse)
def farm_users(request: Request):
    farm_id = CURRENT_FARM_ID.get()
    with SessionLocal() as db:
        rows = db.query(FarmMembership, User).join(User, User.id == FarmMembership.user_id).filter(
            FarmMembership.farm_id == farm_id, FarmMembership.active == 1
        ).order_by(User.full_name, User.email).all()
        return templates.TemplateResponse(request, "farm_users.html", ctx(request, rows=rows, error=""))


@app.post("/admin/users", response_class=HTMLResponse)
def add_farm_user(request: Request, full_name: str = Form(""), email: str = Form(...), role: str = Form("Field Officer"),
                  temporary_password: str = Form("")):
    farm_id = CURRENT_FARM_ID.get()
    allowed_roles = {"Owner", "Manager", "Field Officer", "Viewer"}
    role = role if role in allowed_roles else "Field Officer"
    email_n = _normalise_email(email)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email_n).first()
        if not user:
            if len(temporary_password) < 8:
                rows = db.query(FarmMembership, User).join(User, User.id == FarmMembership.user_id).filter(
                    FarmMembership.farm_id == farm_id, FarmMembership.active == 1).all()
                return templates.TemplateResponse(request, "farm_users.html", ctx(request, rows=rows,
                    error="For a new user, provide a temporary password of at least 8 characters."), status_code=400)
            user = User(email=email_n, full_name=full_name.strip(), password_hash=hash_password(temporary_password), active=1)
            db.add(user); db.flush()
        membership = db.query(FarmMembership).filter(FarmMembership.user_id == user.id, FarmMembership.farm_id == farm_id).first()
        if membership:
            membership.role = role; membership.active = 1
        else:
            db.add(FarmMembership(user_id=user.id, farm_id=farm_id, role=role, active=1))
        db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{membership_id}/role")
def update_farm_user_role(membership_id: int, role: str = Form(...)):
    farm_id = CURRENT_FARM_ID.get()
    allowed_roles = {"Owner", "Manager", "Field Officer", "Viewer"}
    with SessionLocal() as db:
        m = db.query(FarmMembership).filter(FarmMembership.id == membership_id, FarmMembership.farm_id == farm_id).first()
        if m and role in allowed_roles:
            m.role = role; db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{membership_id}/deactivate")
def deactivate_farm_user(membership_id: int, request: Request):
    farm_id = CURRENT_FARM_ID.get()
    with SessionLocal() as db:
        m = db.query(FarmMembership).filter(FarmMembership.id == membership_id, FarmMembership.farm_id == farm_id).first()
        if m and m.user_id != getattr(request.state, "user", None).id:
            m.active = 0; db.commit()
    return RedirectResponse("/admin/users", status_code=303)


# -------------------- Admin / Master Data --------------------
@app.get("/admin/master-data", response_class=HTMLResponse)
def master_data(request: Request):
    with SessionLocal() as db:
        rows = db.query(MasterData).order_by(MasterData.category, MasterData.name).all()
        categories = sorted({r.category for r in rows})
        return templates.TemplateResponse(request, "master_data.html", ctx(request, rows=rows, categories=categories, edit_row=None))


@app.post("/admin/master-data")
def add_master_data(category: str = Form(...), name: str = Form(...), code: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        db.add(MasterData(category=category.strip(), name=name.strip(), code=code.strip(), active=1, notes=notes)); db.commit()
    return RedirectResponse("/admin/master-data", status_code=303)


@app.get("/admin/master-data/{row_id}/edit", response_class=HTMLResponse)
def edit_master_data_page(row_id: int, request: Request):
    with SessionLocal() as db:
        rows = db.query(MasterData).order_by(MasterData.category, MasterData.name).all(); categories=sorted({r.category for r in rows})
        return templates.TemplateResponse(request, "master_data.html", ctx(request, rows=rows, categories=categories, edit_row=db.get(MasterData,row_id)))


@app.post("/admin/master-data/{row_id}/edit")
def update_master_data(row_id: int, category: str = Form(...), name: str = Form(...), code: str = Form(""), notes: str = Form("")):
    with SessionLocal() as db:
        row=db.get(MasterData,row_id)
        if row: row.category,row.name,row.code,row.notes=category.strip(),name.strip(),code.strip(),notes; db.commit()
    return RedirectResponse("/admin/master-data", status_code=303)


@app.post("/admin/master-data/{row_id}/toggle")
def toggle_master_data(row_id: int):
    with SessionLocal() as db:
        row=db.get(MasterData,row_id)
        if row: row.active=0 if row.active else 1; db.commit()
    return RedirectResponse("/admin/master-data", status_code=303)


@app.get("/admin/farm", response_class=HTMLResponse)
def farm_admin(request: Request):
    with SessionLocal() as db:
        return templates.TemplateResponse(request, "farm_admin.html", ctx(request, farm=current_farm(db)))


@app.post("/admin/farm")
def update_farm(name: str = Form(...), location: str = Form(""), total_area_ha: float = Form(0), notes: str = Form("")):
    with SessionLocal() as db:
        farm=current_farm(db); farm.name,farm.location,farm.total_area_ha,farm.notes=name,location,total_area_ha,notes; db.commit()
    return RedirectResponse("/admin/farm", status_code=303)


@app.get("/admin/backup")
def download_backup():
    farm_id = CURRENT_FARM_ID.get()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BACKUPS_DIR / f"FiHay_FRMS_farm_{farm_id}_backup_{stamp}.zip"

    def serialise(obj):
        data = {}
        for col in inspect(obj.__class__).columns:
            value = getattr(obj, col.key)
            if isinstance(value, (date, datetime)):
                value = value.isoformat()
            data[col.key] = value
        return data

    with SessionLocal() as db, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        farm = current_farm(db)
        z.writestr("manifest.json", json.dumps({
            "format": "FiHay FRMS farm backup", "version": "2.0-hosted-pilot",
            "created_at": datetime.utcnow().isoformat(), "farm": serialise(farm) if farm else None
        }, indent=2))
        for model in FARM_SCOPED_MODELS:
            rows = db.query(model).all()
            z.writestr(f"data/{model.__tablename__}.json", json.dumps([serialise(r) for r in rows], indent=2))
        for expense in db.query(Expense).filter(Expense.receipt_filename != "").all():
            f = RECEIPTS_DIR / Path(expense.receipt_filename).name
            if f.exists():
                z.write(f, arcname=f"uploads/receipts/{f.name}")
        for incident in db.query(Incident).filter(Incident.attachment_filename != "").all():
            f = INCIDENTS_DIR / Path(incident.attachment_filename).name
            if f.exists():
                z.write(f, arcname=f"uploads/incidents/{f.name}")
    return FileResponse(out, media_type="application/zip", filename=out.name)


# PWA assets. The service worker lives at the site root so it can cache every module.
@app.get("/sw.js")
def service_worker():
    return FileResponse(BASE_DIR / "static" / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(BASE_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")



@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse(request, "about.html", ctx(request))
