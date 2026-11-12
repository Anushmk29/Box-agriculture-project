"""Bounded, deterministic operational register queries."""
from datetime import date
from typing import Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_


class Page[T](BaseModel):
    items: list[T]
    total: int
    offset: int
    limit: int
    has_more: bool
    scope_label: str | None = None


class RegisterQuery:
    def __init__(self, q: str = Query("", max_length=200),
                 view: Literal["pending", "completed", "all"] = "pending",
                 customer: str = Query("", max_length=200),
                 route: str = Query("", max_length=32),
                 date_from: str = Query("", pattern=r"^$|^\d{4}-\d{2}-\d{2}$"),
                 date_to: str = Query("", pattern=r"^$|^\d{4}-\d{2}-\d{2}$"),
                 status: str = Query("", max_length=64),
                 sort: str = "created_at", direction: Literal["asc", "desc"] = "desc",
                 offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
        self.q, self.view, self.customer, self.route = q.strip(), view, customer.strip(), route.strip()
        self.date_from, self.date_to, self.status = date_from, date_to, status
        self.sort, self.direction, self.offset, self.limit = sort, direction, offset, limit
        for value in (date_from, date_to):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise HTTPException(422, "Invalid shipment date") from exc
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "Start date must be before end date")

    def search(self, query, columns, term=None):
        term = self.q if term is None else term
        if not term:
            return query
        pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return query.filter(or_(*(column.ilike(pattern, escape="\\") for column in columns)))

    def dates(self, query, column):
        if self.date_from:
            query = query.filter(column >= self.date_from)
        if self.date_to:
            query = query.filter(column <= self.date_to)
        return query

    def rows(self, query, sorts, identity):
        if self.sort not in sorts:
            raise HTTPException(422, "Unsupported sort field")
        total = query.with_entities(identity).order_by(None).count()
        column = sorts[self.sort]
        order = column.asc() if self.direction == "asc" else column.desc()
        if self.sort == "shipment_date":
            # ISO dates sort chronologically; undated records belong at the end.
            from sqlalchemy import case
            entity = identity.class_
            ordering = [case((or_(column.is_(None), column == ""), 1), else_=0), order]
            if hasattr(entity, "created_at"):
                ordering.append(entity.created_at.asc())
            ordering.append(identity.asc())
        else:
            ordering = [order, identity.asc()]
        rows = query.order_by(*ordering).offset(self.offset).limit(self.limit).all()
        return rows, total

    def page(self, items, total, *, scope_label=None):
        return Page[dict](items=items, total=total, offset=self.offset, limit=self.limit,
                          has_more=self.offset + len(items) < total, scope_label=scope_label)
