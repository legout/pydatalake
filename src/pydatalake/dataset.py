import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.fs as fs
import pyarrow.feather as pf
import pyarrow.parquet as pq
import polars as pl
import pandas as pd
from pathlib import Path
import duckdb
import uuid
import datetime as dt


class Reader:
    def __init__(
        self,
        path: str,
        partitioning: ds.Partitioning | list[str] | str | None = None,
        filesystem: fs.FileSystem | None = None,
        format: str | None = None,
    ):
        self._path = path
        self._filesystem = filesystem
        self._format = format
        self._partitioning = partitioning
        self.ddb = duckdb.connect()
        self.execute("SET temp_directory='/tmp/duckdb/'")

    def _load_dataset(self, name: str = "pa_dataset", **kwargs):

        self._pa_dataset = ds.dataset(
            source=self._path,
            format=self._format,
            filesystem=self._filesystem,
            partitioning=self._partitioning,
            **kwargs,
        )
        self.ddb.register(name, self._pa_dataset)

    def _load_table(self, name: str = "pa_table", **kwargs):
        if self._format == "parquet":
            self._pa_table = pq.read_table(
                self._path,
                partitioning=self._partitioning,
                filesystem=self._filesystem,
                **kwargs,
            )

        else:
            if self._filesystem is not None:
                if hasattr(self._filesystem, "isfile"):
                    if self._filesystem.isfile(self._path):
                        with self._filesystem.open(self._path) as f:
                            self._pa_table = pf.read_feather(f, **kwargs)
                    else:
                        if not hasattr(self, "_ds"):
                            self._load_dataset()
                        self._pa_table = self._pa_dataset.to_table(**kwargs)
                else:
                    if self._filesystem.get_file_info(self._path).is_file:
                        with self._filesystem.open_input_file(self._path) as f:
                            self._pa_table = pf.read_feather(f, **kwargs)
                    else:
                        if not hasattr(self, "_ds"):
                            self._load_dataset()

                        self._pa_table = self._pa_dataset.to_table(**kwargs)

            else:
                if Path(self._path).is_file():
                    self._pa_table = pf.read_feather(self._path, **kwargs)
                else:
                    if not hasattr(self, "_ds"):
                        self._load_dataset()

                    self._pa_table = self._pa_dataset.to_table(**kwargs)

        self.ddb.register(name, self._pa_table)

    def create_temp_table(self, name: str = "temp_table", **kwargs):
        if hasattr(self, "_pa_table"):
            self.execute(
                f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT * FROM pa_table"
            )

        else:
            if not hasattr(self, "_pa_dataset"):
                self._load_dataset(**kwargs)

            self.execute(
                f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT * FROM pa_dataset"
            )

    def query(self, *args, **kwargs):
        return self.ddb.query(*args, **kwargs)

    def execute(self, *args, **kwargs):
        return self.ddb.execute(*args, **kwargs)

    def filter(self, *args, **kwargs):
        return self.ddb_relation.filter(*args, **kwargs)

    @property
    def pa_dataset(self, **kwargs):
        if not hasattr(self, "_pa_dataset") or len(kwargs) > 0:
            self._load_dataset(**kwargs)

        return self._pa_dataset

    @property
    def pa_table(self, **kwargs):

        if not hasattr(self, "_pa_table") or len(kwargs) > 0:
            name = kwargs.get("name", "temp_table")
            if name in self.execute("SHOW TABLES").df()["name"].tolist():
                self._pa_table = self.query(f"SELECT * FROM {name}").arrow()
            else:
                self._load_table(**kwargs)

        return self._pa_table

    @property
    def ddb_relation(self, **kwargs):
        name = kwargs.get("name", "temp_table")
        if name in self.execute("SHOW TABLES").df()["name"].tolist():
            return self.query(f"SELECT * FROM {name}")

        elif hasattr(self, "_pa_table"):
            return self.ddb.from_arrow(self._pa_table)
        else:
            if not hasattr(self, "_pa_dataset") or len(kwargs) > 0:
                self._load_dataset(**kwargs)

            return self.ddb.from_arrow(self._pa_dataset)

    @property
    def pl_dataframe(self, **kwargs):
        return pl.from_arrow(self.pa_table(**kwargs))


class Writer:
    def __init__(
        self,
        path: str,
        base_name: str = "data",
        partitioning: ds.Partitioning | list[str] | str | None = None,
        with_time_partition: bool = False,
        filesystem: fs.FileSystem | None = None,
        format: str | None = "parquet",
        compression: str | None = "zstd",
    ):
        self._path = path
        self._base_name = base_name
        self._partitioning = partitioning
        self._with_time_partition = with_time_partition
        self._filesystem = filesystem
        self._format = format
        self._compression = compression

    def _gen_path(
        self,
        partition_names: tuple | None = None,
    ):

        parts = [self._path]

        if partition_names is not None:
            parts.extend(partition_names)

        if self._with_time_partition:
            parts.append(str(dt.datetime.today()))

        parts.append(self._base_name)# + f"-{uuid.uuid4().hex}.{self._format}")

        path_ = Path(*parts)

        if self._filesystem is None:
            path_.mkdir.parents(exist_ok=True, parents=True)

        return path

    def _to_duckdbrelation(
        self,
        table: duckdb.DuckDBPyRelation
        | pa.Table
        | ds.Dataset
        | pd.DataFrame
        | pl.DataFrame
        | str,
        # set_temp_table: bool=False,
    ):
        if isinstance(table, pa.Table):
            table_ = self.ddb.from_arrow(table)
        elif isinstance(table, ds.Dataset):
            _table = table
            table_ = self.query("SELECT * FROM _table")
        elif isinstance(table, pd.DataFrame):
            table_ = self.ddb.from_df(table)
        elif isinstance(table, pl.DataFrame):
            table_ = self.ddb.from_arrow(table.to_arrow())
        elif isinstance(table, str):
            if ".parquet" in table:
                table_ = self.ddb.from_parquet(table)
            elif ".csv" in table:
                table_ = self.ddb.from_csv_auto(table)
            else:
                table_ = self.query(f"SELECT * FROM '{table}'")
        else:
            table_ = table

        return table_

    def _make_temp_table(
        self,
        table: duckdb.DuckDBPyRelation
        | pa.Table
        | ds.Dataset
        | pd.DataFrame
        | pl.DataFrame
        | str,
        name: str = "temp_table",
    ):
        table_ = self._to_duckdbrelation(table, use_temp_table=False)
        self.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT * FROM table_")

    def write_table(
        self,
        table: pa.Table,
        path: Path | str,
        **kwargs,
    ):

        filesystem = kwargs.get("filesystem", self._filesystem)
        compression = kwargs.get("compression", self._compression)
        format = (
            kwargs.get("format", self._format)
            .replace("arrow", "feather")
            .replace("ipc", "feather")
        )
        if isinstance(Path, str):
            path = Path(path)

        if path.suffix == "":
            path = path / self._base_name + f"-{uuid.uuid4().hex}.{self._format}"

        if format == "feather":
            if filesystem is not None:
                if hasattr(filesystem, "open"):
                    with filesystem.open(path) as f:
                        pf.write_feather(table, f, compression=compression, **kwargs)
                else:
                    with filesystem.open_output_stream(path) as f:
                        pf.write_feather(table, f, compression=compression, **kwargs)

            else:
                pf.write_feather(
                    table,
                    path,
                    compression=compression,
                    **kwargs,
                )
        else:
            pq.write_table(
                table,
                path,
                compression=compression,
                filesystem=filesystem,
                **kwargs,
            )

    def write_dataset(
        self,
        table: duckdb.DuckDBPyRelation
        | pa.Table
        | ds.Dataset
        | pd.DataFrame
        | pl.DataFrame
        | str,
        path: str | None = None,
        partitioning: list | str | None = None,
        with_time_partition: bool = False,
        n_rows: int | None = None,
        with_temp_table: bool = True,
        **kwargs,
    ):
        
            
        format = kwargs.get("format", self._format)
        compression = kwargs.get("compression", self._compression)
        
        table_ = self._to_duckdbrelation(table=table)
        if with_temp_table:
            self._make_temp_table(table_)
            
        if partitioning is not None:
            if isinstance(repartitioning, str):
                partitioning = [partitioning]
        else:
            partitioning = self._partitioning

        if partitioning is not None:
            partitions = table_.project(",".join(partitioning)).distinct().fetchall()

            for partition_names in partitions:
                path_ = self._gen_path(
                    path=path,
                    partition_names=partition_names,
                )

                filter_ = []
                for p in zip(partitioning, partition_names):
                    filter_.append(f"{p[0]}='{p[1]}'")
                filter_ = " AND ".join(filter_)

                table_part = table_.filter(filter_)

                if n_rows is None:
                    self.write_table(
                        table=table_part.arrow(),
                        path=path_,
                        format=format,
                        compression=compression,
                        **kwargs,
                    )
                else:
                    for i in range(table_part.shape[0] // n_rows + 1):
                        self.write_table(
                            table=table_part.limit(n_rows, offset=i * n_rows).arrow(),
                            path=path_,
                            format=format,
                            compression=compression,
                            **kwargs,
                        )

        else:
            path_ = self._gen_path(
                path=path, partition_names=None
            )
            self.write_table(table=table_.arrow(), path=path_, **kwargs)

        if with_temp_table:
            self.execute("DROP TABLE temp_table")