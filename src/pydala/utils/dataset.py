import polars as pl
import pyarrow as pa


def _pyarrow_schema_auto_conversion(
    schema1: pa.Schema, schema2: pa.Schema
) -> tuple[dict, bool]:
    schema = []
    schemas_equal = True
    dtype_rank = [
        pa.int8(),
        pa.int16(),
        pa.int32(),
        pa.int64(),
        pa.float16(),
        pa.float32(),
        pa.float64(),
        pa.string(),
    ]
    for name in schema1.names:
        type1 = schema1.field(name).type
        type2 = schema2.field(name).type

        if type1 != type2:
            schemas_equal = False
            if type1 in dtype_rank:
                rank1 = dtype_rank.index(type1)
            else:
                rank1 = 0
            if type2 in dtype_rank:
                rank2 = dtype_rank.index(type2)
            else:
                rank2 = 0

            schema.append(pa.field(name, type1 if rank1 > rank2 else type2))

        else:
            schema.append(pa.field(name, type1))

    return pa.schema(schema), schemas_equal


def _polars_schema_auto_conversion(schema1: dict, schema2: dict) -> tuple[dict, bool]:
    schema = {}
    schemas_equal = True
    dtype_rank = [
        pl.Int8(),
        pl.Int16(),
        pl.Int32(),
        pl.Int64(),
        pl.Float32(),
        pl.Float64(),
        pl.Utf8(),
    ]

    for name in schema1:
        type1 = schema1[name]
        type2 = schema2[name]

        if type1 != type2:
            schemas_equal = False
            if type1 in dtype_rank:
                rank1 = dtype_rank.index(type1)
            else:
                rank1 = 0
            if type2 in dtype_rank:
                rank2 = dtype_rank.index(type2)
            else:
                rank2 = 0

            schema[name] = type1 if rank1 > rank2 else type2

        else:
            schema[name] = type1
    return schema, schemas_equal


def schema_auto_conversion(schemas: list[pa.Schema] | list[dict]):
    schemas_equal = True
    schema = schemas[0]
    for schema2 in schemas[1:]:
        schema, schemas_qual_ = (
            _pyarrow_schema_auto_conversion(schema, schema2)
            if isinstance(schema, pa.Schema)
            else _polars_schema_auto_conversion(schema, schema2)
        )

        if not schemas_qual_:
            schemas_equal = schemas_qual_

    return schema, schemas_equal