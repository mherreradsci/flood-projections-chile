"""Test de integración de identificar_zonas_nuevas: rasters sintéticos en
tmp_path, sin datos reales. Ver tests/test_flood_model_integracion.py para
el porqué de esta categoría (documentada en CLAUDE.md).

huella_historica_union.tif ausente ya es el camino normal del código (no
hace falta ningún truco de caché): identificar_zonas_nuevas cae directo a
"sin huella histórica" cuando el archivo no existe.
"""

import geopandas as gpd
import numpy as np
from rasterio.transform import Affine

from inundaciones.new_areas import identificar_zonas_nuevas
from inundaciones.utils import guardar_raster, leer_raster

TRANSFORM = Affine(0.001, 0.0, -71.0, 0.0, -0.001, -30.0)


def _cfg(tmp_path):
    return {
        "rutas": {"data": str(tmp_path / "data"), "outputs": str(tmp_path / "outputs")},
        "region": {"bbox": [-71.01, -30.02, -70.99, -29.98]},  # lat_media = -30.0
    }


def test_separa_nuevas_de_recurrentes_y_descarta_ruido(tmp_path):
    extension = np.zeros((10, 10), dtype="uint8")
    extension[1:5, 1:5] = 1   # bloque A: 16 celdas, sin huella histórica -> "nueva"
    extension[6:10, 6:10] = 1  # bloque B: 16 celdas, con huella histórica -> "recurrente"
    extension[0, 9] = 1        # celda aislada, sin huella -> ruido, bajo AREA_MINIMA_CELDAS

    historico = np.zeros((10, 10), dtype="uint8")
    # la huella cubre el bloque B y una columna extra (5) que nunca está anegada:
    # recurrentes debe ser la intersección con lo anegado, no toda la huella
    historico[6:10, 5:10] = 1

    cfg = _cfg(tmp_path)
    guardar_raster(tmp_path / "outputs" / "extension_test.tif", extension, TRANSFORM,
                   nodata=255, dtype="uint8")
    guardar_raster(tmp_path / "data" / "historical" / "huella_historica_union.tif",
                   historico, TRANSFORM, nodata=255, dtype="uint8")

    rutas = identificar_zonas_nuevas(cfg, sufijo="test")

    nuevas_gdf = gpd.read_file(rutas["zonas_nuevas"])
    recurrentes_gdf = gpd.read_file(rutas["zonas_recurrentes"])
    # el píxel aislado no sobrevive al filtro de área mínima: solo el bloque A queda
    assert len(nuevas_gdf) == 1
    assert len(recurrentes_gdf) == 1

    # el raster (a diferencia del vector) no se filtra por área: conserva el ruido
    nuevas_raster, _, _ = leer_raster(tmp_path / "outputs" / "zonas_nuevas_test.tif")
    recurrentes_raster, _, _ = leer_raster(tmp_path / "outputs" / "zonas_recurrentes_test.tif")
    assert int(nuevas_raster.sum()) == 17  # bloque A (16) + ruido (1)
    assert int(recurrentes_raster.sum()) == 16  # solo la intersección anegada ∩ huella
