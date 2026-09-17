"""Fixtures compartilhadas: banco temporário e cliente de API isolados."""
import os
import tempfile
from pathlib import Path

import pytest

# isola o banco ANTES de importar o app (o caminho é lido no import)
_TMP = tempfile.mkdtemp(prefix="futa-test-")
os.environ["FUTA_DB"] = str(Path(_TMP) / "test.db")
os.environ.pop("APP_TOKEN", None)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app import main

    main.db.init()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def sample_analysis():
    from app.model import TeamSample, analyze_match

    # (dias_atrás, foi_mandante, gols_pro, gols_contra)
    home = TeamSample("Casa FC", [(2, True, 3, 1), (7, False, 2, 0), (11, True, 1, 1),
                                  (16, True, 2, 0), (21, False, 0, 2), (26, True, 1, 1),
                                  (31, False, 2, 1), (36, True, 2, 2), (41, False, 3, 0),
                                  (46, True, 1, 0)])
    away = TeamSample("Fora FC", [(3, False, 2, 1), (8, True, 1, 1), (13, False, 2, 0),
                                  (18, True, 1, 1), (23, False, 3, 0), (28, True, 0, 2),
                                  (33, False, 1, 1), (38, True, 2, 0), (43, False, 1, 1),
                                  (48, True, 1, 1)])
    return analyze_match(home, away)
