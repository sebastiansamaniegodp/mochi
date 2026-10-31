import os
import sys
import socket

from pathlib import Path


class MochiEnvironment:

    def __init__(self):

        # =================================================
        # NOMBRE DE LA PC
        # =================================================

        self.machine_name = (
            socket.gethostname()
        )


        # =================================================
        # CARPETA DONDE ESTÁ MOCHI
        #
        # En desarrollo:
        # carpeta del proyecto.
        #
        # Cuando sea Mochi.exe:
        # carpeta del ejecutable.
        # =================================================

        if getattr(
            sys,
            "frozen",
            False
        ):

            self.app_dir = Path(
                sys.executable
            ).parent

        else:

            self.app_dir = Path(
                __file__
            ).resolve().parent


        # =================================================
        # APPDATA
        #
        # Ejemplo:
        # C:\Users\Usuario\AppData\Roaming\Mochi
        # =================================================

        appdata = os.environ.get(
            "APPDATA"
        )

        if appdata:

            self.data_dir = (
                Path(appdata)
                / "Mochi"
            )

        else:

            self.data_dir = (
                Path.home()
                / ".mochi"
            )


        # =================================================
        # LOCALAPPDATA
        #
        # Archivos grandes o regenerables.
        # =================================================

        local_appdata = os.environ.get(
            "LOCALAPPDATA"
        )


        if local_appdata:

            self.local_data_dir = (
                Path(local_appdata)
                / "Mochi"
            )

        else:

            self.local_data_dir = (
                self.data_dir
                / "local"
            )


        # =================================================
        # SUBCARPETAS
        # =================================================

        self.models_dir = (
            self.local_data_dir
            / "models"
        )

        self.cache_dir = (
            self.local_data_dir
            / "cache"
        )

        self.logs_dir = (
            self.local_data_dir
            / "logs"
        )

        self.database_dir = (
            self.local_data_dir
            / "database"
        )


        # =================================================
        # ARCHIVOS IMPORTANTES
        # =================================================

        self.config_file = (
            self.data_dir
            / "config.json"
        )

        self.file_index_db = (
            self.database_dir
            / "files.db"
        )


        # =================================================
        # CREAR CARPETAS
        # =================================================

        self._create_directories()


    def _create_directories(
        self
    ):

        directories = [

            self.data_dir,
            self.local_data_dir,
            self.models_dir,
            self.cache_dir,
            self.logs_dir,
            self.database_dir

        ]


        for directory in directories:

            directory.mkdir(
                parents=True,
                exist_ok=True
            )


    # =====================================================
    # MOSTRAR INFORMACIÓN
    # =====================================================

    def print_environment(
        self
    ):

        print("")
        print(
            "======================================"
        )
        print(
            "        MOCHI ENVIRONMENT"
        )
        print(
            "======================================"
        )

        print(
            f"PC: {self.machine_name}"
        )

        print(
            f"App: {self.app_dir}"
        )

        print(
            f"Datos: {self.data_dir}"
        )

        print(
            f"Datos locales: {self.local_data_dir}"
        )

        print(
            f"Modelos: {self.models_dir}"
        )

        print(
            f"Cache: {self.cache_dir}"
        )

        print(
            f"Logs: {self.logs_dir}"
        )

        print(
            f"Database: {self.database_dir}"
        )

        print(
            f"Config: {self.config_file}"
        )

        print(
            "======================================"
        )


environment = MochiEnvironment()


if __name__ == "__main__":

    environment.print_environment()
