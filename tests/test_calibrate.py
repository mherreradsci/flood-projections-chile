import numpy as np
import pandas as pd
import pytest

from inundaciones.calibrate import _agregar_factores, _metricas


def test_metricas_caso_conocido():
    modelo = np.array([True, True, False, False])
    observado = np.array([True, False, True, False])
    # tp=1 (idx0), fp=1 (idx1), fn=1 (idx2)
    m = _metricas(modelo, observado)
    assert m["CSI"] == 1 / 3
    assert m["POD"] == 1 / 2
    assert m["FAR"] == 1 / 2


def test_metricas_prediccion_perfecta():
    modelo = np.array([True, True, False, False])
    observado = modelo.copy()
    m = _metricas(modelo, observado)
    assert m["CSI"] == 1.0
    assert m["POD"] == 1.0
    assert m["FAR"] == 0.0


def test_metricas_sin_deteccion_ni_observacion_da_nan():
    modelo = np.array([False, False])
    observado = np.array([False, False])
    m = _metricas(modelo, observado)
    assert np.isnan(m["CSI"])
    assert np.isnan(m["POD"])
    assert np.isnan(m["FAR"])


def test_metricas_sin_positivos_modelados_da_far_nan():
    modelo = np.array([False, False])
    observado = np.array([True, False])
    m = _metricas(modelo, observado)
    assert np.isnan(m["FAR"])  # tp+fp == 0
    assert m["POD"] == 0.0


def test_factor_es_media_geometrica_entre_eventos():
    reporte = pd.DataFrame({"HYBAS_ID": [10, 10], "factor": [2.0, 8.0]})
    factores, _, n_heredadas = _agregar_factores(reporte, f_min=0.5, f_max=5.0)
    assert factores == {"10": pytest.approx(4.0)}  # media geométrica: sqrt(2*8)
    assert n_heredadas == 0


def test_factor_se_recorta_a_los_limites():
    reporte = pd.DataFrame({"HYBAS_ID": [20], "factor": [0.1]})
    factores, _, _ = _agregar_factores(reporte, f_min=0.5, f_max=5.0)
    assert factores["20"] == 0.5


def test_subcuenca_sin_observacion_hereda_la_mediana_regional():
    reporte = pd.DataFrame({
        "HYBAS_ID": [10, 10, 20, 30],
        "factor": [2.0, 8.0, 0.1, np.nan],
    })
    factores, mediana, n_heredadas = _agregar_factores(reporte, f_min=0.5, f_max=5.0)
    # factores observados (ya recortados): {10: 4.0, 20: 0.5} -> mediana 2.25
    assert mediana == pytest.approx(2.25)
    assert factores["30"] == pytest.approx(2.25)
    assert n_heredadas == 1


def test_sin_ninguna_observacion_suficiente_usa_mediana_por_defecto():
    reporte = pd.DataFrame({"HYBAS_ID": [10, 20], "factor": [np.nan, np.nan]})
    factores, mediana, n_heredadas = _agregar_factores(reporte, f_min=0.5, f_max=5.0)
    assert mediana == 1.0
    assert factores == {"10": 1.0, "20": 1.0}
    assert n_heredadas == 2
