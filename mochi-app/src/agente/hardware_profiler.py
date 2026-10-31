import json
import os
import platform
import subprocess

import psutil


# =========================================================
# HARDWARE PROFILER
# =========================================================

class HardwareProfiler:

    def __init__(self):

        self.info = {}


    # =====================================================
    # EJECUTAR COMANDO SIN MOSTRAR VENTANA
    # =====================================================

    def _run_command(
        self,
        command,
        timeout=10
    ):

        try:

            result = subprocess.run(

                command,

                capture_output=True,

                text=True,

                timeout=timeout,

                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if os.name == "nt"
                    else 0
                )
            )


            if result.returncode != 0:

                return None


            output = (
                result.stdout
                .strip()
            )


            if not output:

                return None


            return output


        except Exception:

            return None


    # =====================================================
    # CPU
    # =====================================================

    def _get_cpu_info(self):

        cpu_name = None


        # -------------------------------------------------
        # Intentar obtener nombre real desde Windows
        # -------------------------------------------------

        powershell_script = """
        Get-CimInstance Win32_Processor |
        Select-Object -First 1 -ExpandProperty Name
        """


        output = self._run_command(

            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                powershell_script
            ]
        )


        if output:

            cpu_name = (
                output.strip()
            )


        # -------------------------------------------------
        # Linux: /proc/cpuinfo
        # -------------------------------------------------

        if not cpu_name and os.name != "nt":

            try:

                with open("/proc/cpuinfo", encoding="utf-8") as file:

                    for line in file:

                        if line.startswith("model name"):

                            cpu_name = (
                                line.split(":", 1)[1].strip()
                            )

                            break

            except OSError:
                pass


        # -------------------------------------------------
        # Fallback
        # -------------------------------------------------

        if not cpu_name:

            cpu_name = (

                platform.processor()

                or

                os.environ.get(
                    "PROCESSOR_IDENTIFIER",
                    "Desconocido"
                )
            )


        return {

            "name":
                cpu_name,

            "physical_cores":
                psutil.cpu_count(
                    logical=False
                ),

            "logical_cores":
                psutil.cpu_count(
                    logical=True
                )
        }


    # =====================================================
    # RAM
    # =====================================================

    def _get_ram_info(self):

        memory = (
            psutil.virtual_memory()
        )


        total_gb = round(
            memory.total
            / (1024 ** 3),
            1
        )


        available_gb = round(
            memory.available
            / (1024 ** 3),
            1
        )


        # -------------------------------------------------
        # Detectar clase comercial aproximada
        #
        # Una PC con 16 GB puede aparecer como:
        # 15.7
        # 15.8
        # 15.9
        #
        # por memoria reservada para hardware.
        # -------------------------------------------------

        if total_gb >= 60:

            installed_class_gb = 64


        elif total_gb >= 30:

            installed_class_gb = 32


        elif total_gb >= 15:

            installed_class_gb = 16


        elif total_gb >= 7:

            installed_class_gb = 8


        else:

            installed_class_gb = round(
                total_gb
            )


        return {

            "total_gb":
                total_gb,

            "available_gb":
                available_gb,

            "installed_class_gb":
                installed_class_gb
        }


    # =====================================================
    # GPU NVIDIA MEDIANTE NVIDIA-SMI
    # =====================================================

    def _get_nvidia_gpus(self):

        output = self._run_command(

            [
                "nvidia-smi",

                "--query-gpu="
                "name,"
                "memory.total,"
                "driver_version",

                "--format="
                "csv,noheader,nounits"
            ],

            timeout=10
        )


        if not output:

            return []


        gpus = []


        for line in output.splitlines():

            parts = [

                part.strip()

                for part
                in line.split(",")
            ]


            if len(parts) < 3:

                continue


            name = parts[0]


            try:

                vram_mb = float(
                    parts[1]
                )

                vram_gb = round(
                    vram_mb / 1024,
                    1
                )

            except Exception:

                vram_mb = None
                vram_gb = None


            driver = (
                parts[2]
            )


            gpus.append({

                "name":
                    name,

                "vendor":
                    "NVIDIA",

                "vram_mb":
                    vram_mb,

                "vram_gb":
                    vram_gb,

                "driver":
                    driver,

                "source":
                    "nvidia-smi"
            })


        return gpus


    # =====================================================
    # GPU FALLBACK MEDIANTE WINDOWS
    # =====================================================

    def _get_windows_gpus(self):

        powershell_script = """
        Get-CimInstance Win32_VideoController |
        Select-Object Name, AdapterRAM, DriverVersion |
        ConvertTo-Json -Compress
        """


        output = self._run_command(

            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                powershell_script
            ],

            timeout=10
        )


        if not output:

            return []


        try:

            data = json.loads(
                output
            )

        except Exception:

            return []


        if isinstance(
            data,
            dict
        ):

            data = [
                data
            ]


        gpus = []


        ignored_keywords = [

            "parsec",
            "virtual",
            "remote display",
            "microsoft basic display",
            "indirect display"
        ]


        for gpu in data:

            name = str(
                gpu.get(
                    "Name",
                    ""
                )
            ).strip()


            if not name:

                continue


            normalized_name = (
                name.lower()
            )


            # ---------------------------------------------
            # Ignorar adaptadores virtuales
            # ---------------------------------------------

            if any(

                keyword
                in normalized_name

                for keyword
                in ignored_keywords
            ):

                continue


            adapter_ram = (
                gpu.get(
                    "AdapterRAM"
                )
            )


            vram_gb = None


            if adapter_ram:

                try:

                    vram_gb = round(
                        int(adapter_ram)
                        / (1024 ** 3),
                        1
                    )

                except Exception:

                    pass


            vendor = "Unknown"


            if "nvidia" in normalized_name:

                vendor = "NVIDIA"


            elif (
                "amd" in normalized_name
                or
                "radeon" in normalized_name
            ):

                vendor = "AMD"


            elif "intel" in normalized_name:

                vendor = "Intel"


            gpus.append({

                "name":
                    name,

                "vendor":
                    vendor,

                "vram_mb":
                    None,

                "vram_gb":
                    vram_gb,

                "driver":
                    gpu.get(
                        "DriverVersion"
                    ),

                "source":
                    "windows"
            })


        return gpus


    # =====================================================
    # DETECTAR GPU
    # =====================================================

    def _get_gpu_info(self):

        # -------------------------------------------------
        # NVIDIA-SMI es mucho más fiable para VRAM NVIDIA
        # -------------------------------------------------

        nvidia_gpus = (
            self._get_nvidia_gpus()
        )


        windows_gpus = (
            self._get_windows_gpus()
        )


        final_gpus = []

        seen_names = set()


        # -------------------------------------------------
        # Primero NVIDIA-SMI
        # -------------------------------------------------

        for gpu in nvidia_gpus:

            name_key = (
                gpu["name"]
                .lower()
            )


            seen_names.add(
                name_key
            )


            final_gpus.append(
                gpu
            )


        # -------------------------------------------------
        # Después GPUs que no estén ya detectadas
        # -------------------------------------------------

        for gpu in windows_gpus:

            name_key = (
                gpu["name"]
                .lower()
            )


            duplicate = False


            for seen in seen_names:

                if (
                    name_key in seen
                    or
                    seen in name_key
                ):

                    duplicate = True
                    break


            if duplicate:

                continue


            seen_names.add(
                name_key
            )


            final_gpus.append(
                gpu
            )


        return final_gpus


    # =====================================================
    # SISTEMA OPERATIVO
    # =====================================================

    def _get_os_info(self):

        return {

            "system":
                platform.system(),

            "release":
                platform.release(),

            "version":
                platform.version(),

            "architecture":
                platform.machine()
        }


    # =====================================================
    # PERFIL DE MOCHI
    # =====================================================

    def _determine_mochi_profile(
        self,
        ram
    ):

        installed_class = (
            ram[
                "installed_class_gb"
            ]
        )


        # -------------------------------------------------
        # < 16 GB
        # -------------------------------------------------

        if installed_class < 16:

            return {

                "profile":
                    "unsupported",

                "recommended":
                    False,

                "description":
                    "Menos de 16 GB de RAM"
            }


        # -------------------------------------------------
        # 16 GB
        # -------------------------------------------------

        if installed_class < 32:

            return {

                "profile":
                    "standard",

                "recommended":
                    True,

                "description":
                    "Mochi Standard"
            }


        # -------------------------------------------------
        # 32 GB
        # -------------------------------------------------

        if installed_class < 64:

            return {

                "profile":
                    "performance",

                "recommended":
                    True,

                "description":
                    "Mochi Performance"
            }


        # -------------------------------------------------
        # 64 GB+
        # -------------------------------------------------

        return {

            "profile":
                "high_end",

            "recommended":
                True,

            "description":
                "Mochi High-End"
        }


    # =====================================================
    # ANALIZAR COMPUTADORA
    # =====================================================

    def analyze(self):

        cpu = (
            self._get_cpu_info()
        )


        ram = (
            self._get_ram_info()
        )


        gpu = (
            self._get_gpu_info()
        )


        os_info = (
            self._get_os_info()
        )


        mochi_profile = (
            self._determine_mochi_profile(
                ram
            )
        )


        self.info = {

            "os":
                os_info,

            "cpu":
                cpu,

            "ram":
                ram,

            "gpu":
                gpu,

            "mochi":
                mochi_profile
        }


        return self.info


    # =====================================================
    # MOSTRAR INFORME
    # =====================================================

    def print_report(self):

        info = (
            self.analyze()
        )


        print("")
        print(
            "======================================"
        )
        print(
            "        MOCHI HARDWARE PROFILER"
        )
        print(
            "======================================"
        )


        # =================================================
        # WINDOWS
        # =================================================

        print("")
        print(
            "WINDOWS"
        )


        print(
            f"  Sistema: "
            f"{info['os']['system']}"
        )


        print(
            f"  Versión: "
            f"{info['os']['release']}"
        )


        print(
            f"  Arquitectura: "
            f"{info['os']['architecture']}"
        )


        # =================================================
        # CPU
        # =================================================

        print("")
        print(
            "CPU"
        )


        print(
            f"  Nombre: "
            f"{info['cpu']['name']}"
        )


        print(
            f"  Núcleos físicos: "
            f"{info['cpu']['physical_cores']}"
        )


        print(
            f"  Hilos: "
            f"{info['cpu']['logical_cores']}"
        )


        # =================================================
        # RAM
        # =================================================

        print("")
        print(
            "RAM"
        )


        print(
            f"  Detectada: "
            f"{info['ram']['total_gb']} GB"
        )


        print(
            f"  Clase instalada: "
            f"{info['ram']['installed_class_gb']} GB"
        )


        print(
            f"  Disponible ahora: "
            f"{info['ram']['available_gb']} GB"
        )


        # =================================================
        # GPU
        # =================================================

        print("")
        print(
            "GPU"
        )


        if info["gpu"]:

            for index, gpu in enumerate(
                info["gpu"],
                start=1
            ):

                print(
                    f"  GPU {index}: "
                    f"{gpu['name']}"
                )


                print(
                    f"    Fabricante: "
                    f"{gpu['vendor']}"
                )


                if (
                    gpu["vram_gb"]
                    is not None
                ):

                    print(
                        f"    VRAM: "
                        f"{gpu['vram_gb']} GB"
                    )


                if gpu["driver"]:

                    print(
                        f"    Driver: "
                        f"{gpu['driver']}"
                    )


                print(
                    f"    Fuente: "
                    f"{gpu['source']}"
                )


        else:

            print(
                "  No se detectaron GPUs utilizables"
            )


        # =================================================
        # PERFIL MOCHI
        # =================================================

        print("")
        print(
            "PERFIL MOCHI"
        )


        print(
            f"  Perfil: "
            f"{info['mochi']['profile']}"
        )


        print(
            f"  Descripción: "
            f"{info['mochi']['description']}"
        )


        print(
            f"  Recomendado: "
            f"{info['mochi']['recommended']}"
        )


        print("")
        print(
            "======================================"
        )


# =========================================================
# INSTANCIA GLOBAL
# =========================================================

hardware_profiler = (
    HardwareProfiler()
)


# =========================================================
# EJECUCIÓN DIRECTA
# =========================================================

if __name__ == "__main__":

    hardware_profiler.print_report()