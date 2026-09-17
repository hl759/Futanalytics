"""Caminho do banco: o Render free não tem disco, então FUTA_DB precisa ser tolerante."""
import importlib
from pathlib import Path

import pytest


def test_futa_db_inutilizavel_cai_no_padrao(tmp_path):
    """Regressão: FUTA_DB apontando para um caminho impossível não pode derrubar o app.

    Cenário real: `FUTA_DB=/var/data/...` num plano sem disco persistente.
    """
    from app import db

    arquivo = tmp_path / "nao-e-diretorio.txt"
    arquivo.write_text("x")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FUTA_DB", str(arquivo / "sub" / "banco.db"))
        m = importlib.reload(db)
        assert m.DB_PATH.name == "futanalytics.db"       # caiu no padrão
        assert m.DB_PATH.parent == Path(m.__file__).resolve().parent.parent
    importlib.reload(db)                                  # restaura o banco de teste


def test_futa_db_valido_e_respeitado(tmp_path):
    from app import db

    destino = tmp_path / "sub" / "banco.db"               # diretório é criado
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FUTA_DB", str(destino))
        m = importlib.reload(db)
        assert str(destino) == str(m.DB_PATH) and destino.parent.is_dir()
    importlib.reload(db)
