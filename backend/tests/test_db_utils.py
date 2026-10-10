# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed row access over supabase-py responses (app/services/db_utils.py)."""

from postgrest import APIResponse
from postgrest.base_request_builder import SingleAPIResponse

from app.services.db_utils import row_of, rows_of


def test_rows_of_returns_the_rows_or_an_empty_list():
    assert rows_of(APIResponse(data=[{"id": "a"}, {"id": "b"}])) == [{"id": "a"}, {"id": "b"}]
    assert rows_of(APIResponse(data=[])) == []


def test_row_of_handles_a_missing_response_and_an_empty_row():
    assert row_of(None) is None  # maybe_single() found nothing
    assert row_of(SingleAPIResponse(data={})) is None
    assert row_of(SingleAPIResponse(data={"id": "a"})) == {"id": "a"}
