import os
from typing import Optional

import fire
import pandas as pd
from loguru import logger
from tqdm import tqdm

import qlib
from qlib.data import D


class DataHealthChecker:
    """Checks a dataset for data completeness and correctness. The data will be converted to a pd.DataFrame and checked for the following problems:
    - any of the columns ["open", "high", "low", "close", "volume"] are missing
    - any data is missing
    - any step change in the OHLCV columns is above a threshold (default: 0.5 for price, 3 for volume)
    - any factor is missing
    """

    def __init__(
        self,
        csv_path=None,
        qlib_dir=None,
        freq="day",
        large_step_threshold_price=0.5,
        large_step_threshold_volume=3,
        missing_data_num=0,
    ):
        assert csv_path or qlib_dir, "One of csv_path or qlib_dir should be provided."
        assert not (csv_path and qlib_dir), "Only one of csv_path or qlib_dir should be provided."

        self.data = {}
        self.problems = {}
        self.freq = freq
        self.large_step_threshold_price = large_step_threshold_price
        self.large_step_threshold_volume = large_step_threshold_volume
        self.missing_data_num = missing_data_num
        self.qlib_dir = os.path.abspath(os.path.expanduser(qlib_dir)) if qlib_dir else None

        if csv_path:
            csv_path = os.path.abspath(os.path.expanduser(csv_path))
            assert os.path.isdir(csv_path), f"{csv_path} should be a directory."
            files = [f for f in os.listdir(csv_path) if f.endswith(".csv")]
            for filename in tqdm(files, desc="Loading data"):
                df = pd.read_csv(os.path.join(csv_path, filename))
                self.data[filename] = df

        elif qlib_dir:
            qlib.init(provider_uri=qlib_dir)
            self.load_qlib_data()

    def load_qlib_data(self):
        instruments = D.instruments(market="all")
        instrument_list = D.list_instruments(instruments=instruments, as_list=True, freq=self.freq)
        required_fields = ["$open", "$close", "$low", "$high", "$volume", "$factor"]
        df = None
        for instrument in instrument_list:
            df = D.features([instrument], required_fields, freq=self.freq)
            df.rename(
                columns={
                    "$open": "open",
                    "$close": "close",
                    "$low": "low",
                    "$high": "high",
                    "$volume": "volume",
                    "$factor": "factor",
                },
                inplace=True,
            )
            self.data[instrument] = df
        if df is not None:
            print(df)

    # NOTE:
    # This check is added due to a known issue in Qlib where feature paths
    # are constructed using lowercased instrument names. On case-sensitive
    # file systems (e.g. Linux), uppercase directory names under `features/`
    # will cause data loading failures.
    #
    # See: https://github.com/microsoft/qlib/issues/2053
    def check_features_dir_lowercase(self) -> Optional[pd.DataFrame]:
        """
        Check whether all subdirectories under `<qlib_dir>/features` are named in lowercase.

        This validation helps prevent data loading issues on case-sensitive
        file systems caused by uppercase instrument directory names.
        """
        if not self.qlib_dir:
            return None

        features_dir = os.path.join(self.qlib_dir, "features")
        if not os.path.isdir(features_dir):
            logger.warning(f"`features` directory not found under {self.qlib_dir}")
            return None

        bad_dirs = []
        for name in os.listdir(features_dir):
            full_path = os.path.join(features_dir, name)
            if os.path.isdir(full_path) and name != name.lower():
                bad_dirs.append(name)

        if bad_dirs:
            result_df = pd.DataFrame({"non_lowercase_dir": bad_dirs})
            return result_df
        else:
            logger.info(
                f"✅ All subdirectories under `{os.path.join(self.qlib_dir, 'features')}` are named in lowercase."
            )
            return None

    def check_missing_data(self) -> Optional[pd.DataFrame]:
        """Check if any data is missing in the DataFrame."""
        result_dict = {
            "instruments": [],
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
        }
        for filename, df in self.data.items():
            missing_data_columns = df.isnull().sum()[df.isnull().sum() > self.missing_data_num].index.tolist()
            if len(missing_data_columns) > 0:
                result_dict["instruments"].append(filename)
                result_dict["open"].append(int(df["open"].isnull().sum()) if "open" in df.columns else None)
                result_dict["high"].append(int(df["high"].isnull().sum()) if "high" in df.columns else None)
                result_dict["low"].append(int(df["low"].isnull().sum()) if "low" in df.columns else None)
                result_dict["close"].append(int(df["close"].isnull().sum()) if "close" in df.columns else None)
                result_dict["volume"].append(int(df["volume"].isnull().sum()) if "volume" in df.columns else None)

        result_df = pd.DataFrame(result_dict).set_index("instruments")
        if not result_df.empty:
            return result_df
        else:
            logger.info(f"✅ There are no missing data.")
            return None

    def check_large_step_changes(self) -> Optional[pd.DataFrame]:
        """Check if there are any large step changes above the threshold in the OHLCV columns."""
        result_dict = {
            "instruments": [],
            "col_name": [],
            "date": [],
            "pct_change": [],
        }
        for filename, df in self.data.items():
            affected_columns = []
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    pct_change = df[col].pct_change(fill_method=None).abs()
                    threshold = self.large_step_threshold_volume if col == "volume" else self.large_step_threshold_price
                    if pct_change.max() > threshold:
                        large_steps = pct_change[pct_change > threshold]
                        result_dict["instruments"].append(filename)
                        result_dict["col_name"].append(col)
                        result_dict["date"].append(large_steps.index.to_list()[0][1].strftime("%Y-%m-%d"))
                        result_dict["pct_change"].append(pct_change.max())
                        affected_columns.append(col)

        result_df = pd.DataFrame(result_dict).set_index("instruments")
        if not result_df.empty:
            return result_df
        else:
            logger.info(f"✅ There are no large step changes in the OHLCV column above the threshold.")
            return None

    def check_required_columns(self) -> Optional[pd.DataFrame]:
        """Check if any of the required columns (OLHCV) are missing in the DataFrame."""
        required_columns = ["open", "high", "low", "close", "volume"]
        rows = []
        for filename, df in self.data.items():
            missing_required_columns = [column for column in required_columns if column not in df.columns]
            if missing_required_columns:
                rows.append({"instruments": filename, "missing_col": ",".join(missing_required_columns)})

        result_df = pd.DataFrame(rows, columns=["instruments", "missing_col"]).set_index("instruments")
        if not result_df.empty:
            return result_df
        else:
            logger.info(f"✅ The columns (OLHCV) are complete and not missing.")
            return None

    def check_missing_factor(self) -> Optional[pd.DataFrame]:
        """Check if the 'factor' column is missing in the DataFrame."""
        rows = []
        for filename, df in self.data.items():
            if "000300" in filename or "000903" in filename or "000905" in filename:
                continue
            missing_col = "factor" not in df.columns
            missing_data = False if missing_col else bool(df["factor"].isnull().all())
            if missing_col or missing_data:
                rows.append({
                    "instruments": filename,
                    "missing_factor_col": missing_col,
                    "missing_factor_data": missing_data,
                })

        result_df = pd.DataFrame(
            rows, columns=["instruments", "missing_factor_col", "missing_factor_data"]
        ).set_index("instruments")
        if not result_df.empty:
            return result_df
        else:
            logger.info(f"✅ The `factor` column already exists and is not empty.")
            return None

    def check_data(self):
        check_missing_data_result = self.check_missing_data()
        check_large_step_changes_result = self.check_large_step_changes()
        check_required_columns_result = self.check_required_columns()
        check_missing_factor_result = self.check_missing_factor()
        check_features_dir_case_result = self.check_features_dir_lowercase()
        if (
            check_missing_data_result is not None
            or check_large_step_changes_result is not None
            or check_required_columns_result is not None
            or check_missing_factor_result is not None
            or check_features_dir_case_result is not None
        ):
            print(f"\nSummary of data health check ({len(self.data)} files checked):")
            print("-------------------------------------------------")
            if isinstance(check_missing_data_result, pd.DataFrame):
                logger.warning(f"There is missing data.")
                print(check_missing_data_result)
            if isinstance(check_large_step_changes_result, pd.DataFrame):
                logger.warning(f"The OHLCV column has large step changes.")
                print(check_large_step_changes_result)
            if isinstance(check_required_columns_result, pd.DataFrame):
                logger.warning(f"Columns (OLHCV) are missing.")
                print(check_required_columns_result)
            if isinstance(check_missing_factor_result, pd.DataFrame):
                logger.warning(f"The factor column does not exist or is empty")
                print(check_missing_factor_result)
            if isinstance(check_features_dir_case_result, pd.DataFrame):
                logger.warning(
                    f"Some subdirectories under `{os.path.join(self.qlib_dir, 'features')}` contain uppercase letters, please rename them to lowercase manually."
                )
                print(check_features_dir_case_result)


if __name__ == "__main__":
    fire.Fire(DataHealthChecker)
