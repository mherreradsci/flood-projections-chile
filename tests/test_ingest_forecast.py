from datetime import datetime, timezone

import numpy as np
import pytest

from inundaciones.ingest_forecast import (
    _estado_ciclo,
    _isoterma0_desde_perfil,
    _ultimo_ciclo_gfs,
    escenarios_disponibles,
    validar_escenario,
)


def test_interpola_linealmente_entre_dos_niveles():
    z = np.array([0.0, 1000.0])
    t = np.array([275.15, 269.15])  # 2 °C en superficie, -4 °C a 1000 m
    assert _isoterma0_desde_perfil(t, z, defecto_m=3000.0) == pytest.approx(333.33, abs=0.1)


def test_ordena_el_perfil_antes_de_interpolar():
    # mismos niveles que el caso anterior, pero desordenados (z decreciente)
    z = np.array([1000.0, 0.0])
    t = np.array([269.15, 275.15])
    assert _isoterma0_desde_perfil(t, z, defecto_m=3000.0) == pytest.approx(333.33, abs=0.1)


def test_sin_cruce_devuelve_el_defecto():
    z = np.array([0.0, 500.0, 1000.0])
    t = np.array([300.15, 295.15, 290.15])  # siempre sobre 0 °C
    assert _isoterma0_desde_perfil(t, z, defecto_m=2500.0) == 2500.0


def test_con_inversion_termica_toma_el_cruce_mas_alto():
    # superficie fría, capa cálida intermedia (inversión), fría de nuevo en altura:
    # cruza 0 °C dos veces; debe quedarse con el más alto de los dos.
    z = np.array([0.0, 500.0, 1000.0, 1500.0])
    t = np.array([271.15, 276.15, 274.15, 270.15])  # -2, +3, +1, -3 °C
    assert _isoterma0_desde_perfil(t, z, defecto_m=3000.0) == pytest.approx(1125.0, abs=0.1)


def test_ciclo_completo_cuando_llega_al_horizonte_pedido():
    def existe(fxx):
        return fxx == 24

    assert _estado_ciclo(existe, horas=24) == {"completo": True, "ultima_fxx": 24}


def test_ciclo_no_publicado_da_ultima_fxx_none():
    def existe(fxx):
        return False

    assert _estado_ciclo(existe, horas=24) == {"completo": False, "ultima_fxx": None}


def test_ciclo_parcial_ubica_el_ultimo_paso_disponible():
    def existe(fxx):
        # publicado hasta f012 nomás: f000 y f012 existen, f018 y f024 no
        return fxx in (0, 12)

    assert _estado_ciclo(existe, horas=24) == {"completo": False, "ultima_fxx": 12}


def test_ciclo_parcial_sin_ningun_paso_intermedio_disponible():
    def existe(fxx):
        # solo f000 existe: el escaneo hacia atrás no encuentra nada
        return fxx == 0

    assert _estado_ciclo(existe, horas=24) == {"completo": False, "ultima_fxx": 0}


def test_ultimo_ciclo_gfs_resta_el_rezago_y_redondea_a_6_horas():
    ahora = datetime(2026, 7, 22, 3, 30, tzinfo=timezone.utc)  # -5h -> 21 jul 22:30
    assert _ultimo_ciclo_gfs(ahora) == datetime(2026, 7, 21, 18, 0, tzinfo=timezone.utc)


def test_ultimo_ciclo_gfs_en_un_limite_exacto():
    ahora = datetime(2026, 7, 22, 5, 0, tzinfo=timezone.utc)  # -5h -> 22 jul 00:00
    assert _ultimo_ciclo_gfs(ahora) == datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)


# --- validación de escenarios -------------------------------------------------
# `04_proyectar.py --fuente escenario --escenario xxx` con un nombre inexistente
# reventaba con un KeyError pelado dentro de generar_escenario, sin decir qué
# escenarios existían.

CFG_ESCENARIOS = {
    "escenarios": {
        "extremo_200mm": {"precipitacion_mm": 200, "horas": 72, "isoterma0_m": 3200},
        "moderado_100mm": {"precipitacion_mm": 100, "horas": 72, "isoterma0_m": 2800},
    }
}


def test_escenario_valido_devuelve_su_definicion():
    esc = validar_escenario(CFG_ESCENARIOS, "extremo_200mm")
    assert esc["precipitacion_mm"] == 200
    assert esc["isoterma0_m"] == 3200


def test_escenario_inexistente_falla_con_valueerror():
    with pytest.raises(ValueError):
        validar_escenario(CFG_ESCENARIOS, "no_existe")


def test_el_error_nombra_el_escenario_pedido_y_lista_los_validos():
    """El mensaje es la razón de ser del cambio: sin él no se sabe qué poner."""
    with pytest.raises(ValueError) as exc:
        validar_escenario(CFG_ESCENARIOS, "extremo_300mm")
    msg = str(exc.value)
    assert "extremo_300mm" in msg
    assert "extremo_200mm" in msg and "moderado_100mm" in msg


def test_config_sin_escenarios_no_revienta_y_lo_dice():
    """Un config regional puede no definir escenarios; el error debe explicarlo."""
    for cfg in ({}, {"escenarios": None}, {"escenarios": {}}):
        with pytest.raises(ValueError) as exc:
            validar_escenario(cfg, "extremo_200mm")
        assert "ninguno definido" in str(exc.value)


def test_es_sensible_a_mayusculas_y_espacios():
    """Los nombres son claves de YAML: no se normalizan."""
    for nombre in ("Extremo_200mm", " extremo_200mm", "extremo_200mm "):
        with pytest.raises(ValueError):
            validar_escenario(CFG_ESCENARIOS, nombre)


def test_escenarios_disponibles_ordenados():
    assert escenarios_disponibles(CFG_ESCENARIOS) == ["extremo_200mm", "moderado_100mm"]


@pytest.mark.parametrize("cfg", [{}, {"escenarios": None}, {"escenarios": {}}])
def test_escenarios_disponibles_vacio_sin_definiciones(cfg):
    assert escenarios_disponibles(cfg) == []
