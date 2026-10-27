from sqlalchemy import Column, Integer, String, Text, JSON, DateTime, ForeignKey, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class BinderLayout(Base):
    __tablename__ = "binder_layouts"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, default="Untitled Binder")
    cols = Column(Integer, default=3)
    rows = Column(Integer, default=3)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pages = relationship("BinderPage", back_populates="layout", cascade="all, delete-orphan")
    custom_groups = relationship("CustomCardGroup", back_populates="layout", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "cols": self.cols,
            "rows": self.rows,
            "pages": [p.to_dict() for p in self.pages],
            "custom_groups": {g.group_id: g.to_dict() for g in self.custom_groups},
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class BinderPage(Base):
    __tablename__ = "binder_pages"

    id = Column(Integer, primary_key=True)
    layout_id = Column(Integer, ForeignKey("binder_layouts.id"), nullable=False)
    page_number = Column(Integer, nullable=False)
    label = Column(String(255), default="")

    layout = relationship("BinderLayout", back_populates="pages")
    slots = relationship("BinderSlot", back_populates="page", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "label": self.label,
            "slots": [s.to_dict() for s in self.slots],
        }


class BinderSlot(Base):
    __tablename__ = "binder_slots"

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey("binder_pages.id"), nullable=False)
    slot_index = Column(Integer, nullable=False)
    card_url = Column(Text, default="")
    card_info = Column(JSON, default={})

    page = relationship("BinderPage", back_populates="slots")

    def to_dict(self):
        return {
            "url": self.card_url,
            "info": self.card_info,
        }

class CardURL(Base):
    __tablename__ = "card_urls"

    id = Column(Integer, primary_key=True)
    url = Column(String(512), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
class CachedCard(Base):
    __tablename__ = "cached_cards"

    id = Column(Integer, primary_key=True)
    card_url = Column(String(512), unique=True, nullable=False, index=True)
    image_data = Column(LargeBinary, nullable=False)
    set_code = Column(String(32), nullable=False)
    tcgplayer_set_code = Column(String(32))
    set_name = Column(String(255))
    release_date = Column(DateTime)
    number = Column(Integer)
    card_name = Column(String(255))
    card_type = Column(String(255))
    rarity = Column(String(64))
    usd_price = Column(Integer)
    eur_price = Column(Integer)
    tcgplayer_url = Column(String(512))
    cardmarket_url = Column(String(512))
    symbol_bytes = Column(LargeBinary)
    image_url = Column(String(512))
    lang = Column(String(8))
    created_at = Column(DateTime, default=datetime.utcnow)

class CardPrices(Base):
    __tablename__ = "card_price_data"

    id = Column(Integer, primary_key=True)
    card_url = Column(String(512), unique=True, nullable=False, index=True)
    tcgplayer_price_data = Column(JSON, nullable=False)
    cm_price_data = Column(JSON, nullable=False)
    
class CustomCardGroup(Base):
    __tablename__ = "custom_card_groups"

    id = Column(Integer, primary_key=True)
    layout_id = Column(Integer, ForeignKey("binder_layouts.id"), nullable=False)
    group_id = Column(String(8), nullable=False)
    label = Column(String(255), nullable=False)
    tiles = Column(JSON, default=[])  # list of urls
    cols = Column(Integer, default=1)
    rows = Column(Integer, default=1)

    layout = relationship("BinderLayout", back_populates="custom_groups")

    def to_dict(self):
        return {
            "label": self.label,
            "tiles": self.tiles,
            "cols": self.cols,
            "rows": self.rows,
        }
