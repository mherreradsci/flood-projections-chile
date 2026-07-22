import geopandas as gpd
from shapely.geometry import box

from inundaciones.aoi import _subcuencas_en_region

REGION = gpd.GeoDataFrame(geometry=[box(0, 0, 10, 10)], crs="EPSG:4326")


def _cuencas(*, hybas_ids, geoms):
    return gpd.GeoDataFrame(
        {
            "HYBAS_ID": hybas_ids,
            "NEXT_DOWN": [0] * len(hybas_ids),
            "SUB_AREA": [1.0] * len(hybas_ids),
            "otra_columna": ["x"] * len(hybas_ids),  # no debe sobrevivir al filtro
        },
        geometry=geoms,
        crs="EPSG:4326",
    )


def test_conserva_solo_las_subcuencas_dentro_de_la_region():
    cuencas = _cuencas(
        hybas_ids=[1.0, 2.0],
        geoms=[box(1, 1, 3, 3), box(20, 20, 22, 22)],  # dentro, fuera
    )
    resultado = _subcuencas_en_region(cuencas, REGION)
    assert list(resultado.HYBAS_ID) == [1]


def test_hybas_id_queda_como_int64():
    cuencas = _cuencas(hybas_ids=[1.0], geoms=[box(1, 1, 3, 3)])
    resultado = _subcuencas_en_region(cuencas, REGION)
    assert resultado.HYBAS_ID.dtype == "int64"


def test_descarta_columnas_fuera_del_contrato():
    cuencas = _cuencas(hybas_ids=[1.0], geoms=[box(1, 1, 3, 3)])
    resultado = _subcuencas_en_region(cuencas, REGION)
    assert list(resultado.columns) == ["HYBAS_ID", "NEXT_DOWN", "SUB_AREA", "geometry"]


def test_si_ninguna_cae_dentro_conserva_todas():
    # ninguna subcuenca tiene su punto representativo dentro de la región:
    # en vez de devolver un conjunto vacío, se conservan todas
    cuencas = _cuencas(
        hybas_ids=[1.0, 2.0],
        geoms=[box(20, 20, 22, 22), box(30, 30, 32, 32)],
    )
    resultado = _subcuencas_en_region(cuencas, REGION)
    assert list(resultado.HYBAS_ID) == [1, 2]
