# Gently — Hoja de ruta de implementación

## Contexto y objetivo

Gently es un instalador de Gentoo Linux escrito en Python. Opera en dos fases
bien separadas:

1. **Recolección de datos**: recorre las secciones de configuración en orden.
   Si una sección tiene todos sus datos obligatorios presentes, la salta. Si no,
   muestra un formulario para esa sección pre-rellenado con los valores que ya
   existen. Cuando el usuario completa el formulario, pasa a la siguiente sección.
2. **Ejecución**: con todos los datos garantizadamente completos y validados,
   ejecuta la instalación de forma totalmente desatendida.

El fichero de configuración es `config.toml`. Su schema está documentado en el
propio fichero de referencia del proyecto. Gently acepta un config parcial o
vacío; lo que falte se pide en la fase de recolección.

---

## Restricciones del entorno de ejecución

Gently debe poder ejecutarse desde el disco de instalación de Gentoo sin
instalar nada previamente en el entorno live. Esto implica:

- **Python 3.11+** (disponible en el entorno live de Gentoo).
- **Sin dependencias del sistema no garantizadas**. Todo lo que no esté en la
  stdlib de Python se bundlea dentro del propio repositorio de Gently en
  `vendor/`.
- **Dependencias bundleadas**:
  - `tomli_w`: serialización de TOML (~15KB). Lectura con `tomllib` (stdlib).
  - `curses`: interfaz de terminal. Está en la stdlib de Python pero requiere
    que el sistema tenga `libncurses`. En el entorno live de Gentoo está
    garantizado.

---

## Decisiones de diseño fijas

- **Formato de configuración**: TOML
  - Lectura: `tomllib` (stdlib 3.11+)
  - Escritura: `tomli_w` (bundleado en `vendor/`)
- **Modelo de datos**: `dataclasses` de stdlib. La validación se implementa
  a mano en `model/validators.py`.
- **Interfaz de terminal**: `curses` con arquitectura de backend intercambiable.
  Los formularios son independientes del backend de UI. Cambiar de backend
  en el futuro requiere solo implementar la interfaz abstracta y cambiar
  un parámetro en el arranque.
- **Punto de entrada**: `gently.py`

---

## Estructura del proyecto

```
gently/
├── gently.py                   # Punto de entrada
├── config.toml                 # Config del usuario (puede estar vacío o parcial)
├── vendor/
│   └── tomli_w/                # Dependencia bundleada
├── model/
│   ├── __init__.py
│   ├── config.py               # Dataclasses del schema completo
│   └── validators.py           # Validaciones de coherencia entre secciones
├── ui/
│   ├── __init__.py
│   ├── abstract.py             # Interfaz abstracta UIBackend
│   ├── curses_backend.py       # Implementación con curses
│   └── forms/
│       ├── __init__.py
│       ├── base.py             # Clase base SectionForm
│       ├── system.py
│       ├── stage3.py
│       ├── disks.py
│       ├── portage.py
│       ├── kernel.py
│       ├── bootloader.py
│       ├── services.py
│       ├── users.py
│       ├── packages.py
│       └── distcc.py
├── installer/
│   ├── __init__.py
│   ├── runner.py               # Orquestador de fases
│   ├── preflight.py
│   ├── partition.py
│   ├── stage3.py
│   ├── portage.py
│   ├── kernel.py
│   ├── system.py
│   ├── services.py
│   ├── users.py
│   ├── bootloader.py
│   └── packages.py
└── util/
    ├── __init__.py
    └── log.py
```

---

## Milestone 0 — Infraestructura base

**Objetivo**: tener el esqueleto del proyecto operativo. Al final de este
milestone se puede cargar un `config.toml` y obtener un objeto Python validado,
y serializar ese objeto de vuelta a TOML.

### Tarea 0.1 — Bundling de dependencias

Copiar `tomli_w` en `vendor/tomli_w/`. Añadir al inicio de `gently.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor"))
```

Con eso todos los módulos del proyecto pueden hacer `import tomli_w` sin que
esté instalado en el sistema.

### Tarea 0.2 — Modelo de datos

Implementar en `model/config.py` los dataclasses que representan el schema
completo de `config.toml`. Cada sección del TOML es un dataclass separado.

Reglas:
- Los campos opcionales usan `field(default=None)` con tipo `T | None`.
- Los campos con valor por defecto razonable llevan ese valor.
- Los campos obligatorios sin valor por defecto usan `field(default=None)`
  igualmente — la obligatoriedad la determina el formulario, no el modelo.
  El modelo nunca lanza errores por campos ausentes.
- Todos los modelos implementan un método `to_dict() -> dict` que omite
  los campos `None` en el resultado.

Dataclasses a implementar:

```
SystemConfig
Stage3Config
PartitionConfig
DiskConfig
PortagePackagesConfig
PortageRepoConfig
PortageConfig
KernelConfig
KernelCustomConfig
BootloaderGrubConfig
BootloaderConfig
NetworkInterfaceConfig
NetworkConfig
ServicesRolesConfig
ServicesConfig
UserAccountConfig
UsersConfig
PackagesConfig
DistccConfig
GentlyConfig              # raíz que agrupa todo lo anterior
```

### Tarea 0.3 — Cargador de config

Implementar en `model/config.py` la función:

```python
def load_config(path: str) -> GentlyConfig:
    """
    Carga un config.toml parcial o completo.
    Los campos ausentes quedan como None en el modelo.
    Lanza ConfigError si el TOML está malformado o contiene
    valores de tipo incorrecto.
    Si el fichero no existe, devuelve un GentlyConfig vacío.
    """
```

### Tarea 0.4 — Escritor de config

Implementar en `model/config.py` la función:

```python
def save_config(config: GentlyConfig, path: str) -> None:
    """
    Serializa el GentlyConfig completo a TOML y lo escribe en path.
    Los campos None se omiten.
    Usa tomli_w de vendor/.
    """
```

### Tarea 0.5 — Validaciones de coherencia

Implementar en `model/validators.py`:

```python
def validate_coherence(config: GentlyConfig) -> list[str]:
    """
    Valida coherencia entre secciones. Se llama justo antes de iniciar
    la instalación, no durante la carga.
    Devuelve lista de mensajes de error. Lista vacía = config coherente.
    """
```

Validaciones a implementar:
- Si `boot_mode = "uefi"` en algún disco, debe haber exactamente una partición
  con `"esp"` en flags en ese disco.
- Si `distcc.enabled = true`, `distcc.hosts` no puede estar vacío.
- Si `kernel.method` es `"menuconfig"` o `"custom"`, debe existir
  `kernel.custom.config_path`.
- Si `bootloader.type = "grub"`, debe existir la sección `[bootloader.grub]`.
- Si el variant del stage3 contiene `"systemd"`, los roles `logging` y `ntp`
  de `services.roles` deben ser `"none"` o estar ausentes.
- En v1: si hay más de un disco en `disks`, error con mensaje explícito.

### Tarea 0.6 — Logger

Implementar en `util/log.py` un logger con niveles INFO, WARN, ERROR y DEBUG.
Escribe simultáneamente a stdout y a `/tmp/gently.log`.
Desactiva colores ANSI automáticamente si stdout no es un terminal.
No usa el módulo `logging` de stdlib para mantener el control total del formato.

---

## Milestone 1 — Arquitectura de UI

**Objetivo**: definir la interfaz abstracta de UI e implementar el backend de
curses. Al final de este milestone se puede mostrar un formulario de prueba
con campos editables.

### Tarea 1.1 — Interfaz abstracta

Implementar en `ui/abstract.py`:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class FieldSpec:
    """
    Descripción abstracta de un campo de formulario.
    El backend de UI lo renderiza como considere apropiado.
    """
    key: str                          # identificador interno
    label: str                        # etiqueta visible
    type: str                         # "text" | "password" | "choice" |
                                      # "bool" | "list" | "int"
    default: Any = None               # valor pre-rellenado
    options: list[str] | None = None  # para type="choice"
    help: str | None = None           # texto de ayuda al pulsar "?"
    required: bool = True

@dataclass
class FormSpec:
    """
    Descripción abstracta de un formulario completo.
    """
    title: str
    subtitle: str | None
    fields: list[FieldSpec]

class UIBackend(ABC):
    """
    Interfaz que todo backend de UI debe implementar.
    """

    @abstractmethod
    def show_form(self, form: FormSpec) -> dict[str, Any]:
        """
        Muestra el formulario y devuelve un dict con los valores
        introducidos por el usuario, indexados por FieldSpec.key.
        Los campos no modificados devuelven su default.
        """

    @abstractmethod
    def show_summary(self, sections: list[tuple[str, dict]]) -> str:
        """
        Muestra el resumen completo de la configuración.
        Devuelve la acción elegida por el usuario:
        "install" | "edit:<section_key>" | "save_and_exit"
        """

    @abstractmethod
    def show_progress(self, phase: str, message: str) -> None:
        """
        Muestra el progreso durante la instalación.
        """

    @abstractmethod
    def show_error(self, message: str) -> None:
        """
        Muestra un error bloqueante.
        """

    @abstractmethod
    def show_confirm(self, message: str) -> bool:
        """
        Muestra un diálogo de confirmación sí/no.
        """

    @abstractmethod
    def show_info(self, title: str, lines: list[str]) -> None:
        """
        Muestra información no interactiva (sección completada, aviso, etc.)
        """
```

### Tarea 1.2 — Backend curses

Implementar en `ui/curses_backend.py` la clase `CursesBackend(UIBackend)`.

Comportamiento esperado de `show_form`:
- Muestra el título del formulario en la línea superior.
- Lista los campos verticalmente. El campo activo se resalta.
- Navegación con flechas arriba/abajo o Tab/Shift-Tab.
- Para `type="text"` e `type="int"`: edición inline del valor.
- Para `type="password"`: edición inline sin eco.
- Para `type="choice"`: abre un submenú con las opciones al pulsar Enter.
- Para `type="bool"`: toggle con espacio o Enter.
- Para `type="list"`: abre una pantalla secundaria donde se pueden añadir
  y eliminar valores.
- "?" muestra el texto de ayuda del campo si existe.
- F10 o Ctrl+S confirma el formulario.
- Esc cancela (vuelve al estado anterior sin guardar cambios).
- Redimensión de terminal: se redibuja el formulario correctamente.

Comportamiento esperado de `show_summary`:
- Muestra las secciones con sus valores en una pantalla scrollable.
- Tres opciones en la parte inferior: Instalar / Editar sección / Guardar y salir.

Comportamiento esperado de `show_progress`:
- Línea de fase actual + mensaje de progreso.
- Área de log scrollable con la salida de los comandos en ejecución.

### Tarea 1.3 — Instanciación del backend

En `gently.py`, el backend se instancia una vez al arranque y se pasa a todos
los componentes que lo necesitan:

```python
def main():
    backend = CursesBackend()
    config = load_config("config.toml")
    config = collect(config, backend)
    save_config(config, "config.toml")
    errors = validate_coherence(config)
    if errors:
        backend.show_error("\n".join(errors))
        sys.exit(1)
    run_installation(config, backend)
```

Para cambiar de backend en el futuro basta con sustituir `CursesBackend()`
por otra clase que implemente `UIBackend`.

---

## Milestone 2 — Motor de formularios

**Objetivo**: el motor que recorre las secciones y decide cuáles necesitan
formulario.

### Clase base `SectionForm` en `ui/forms/base.py`

```python
class SectionForm:
    section_name: str    # nombre legible, ej: "Configuración del sistema"
    section_key: str     # clave en GentlyConfig, ej: "system"

    def is_complete(self, config: GentlyConfig) -> bool:
        """
        True si la sección tiene todos los campos obligatorios.
        Los campos opcionales nunca bloquean.
        Cada subclase implementa su lógica.
        """
        raise NotImplementedError

    def build_form(self, config: GentlyConfig) -> FormSpec:
        """
        Construye el FormSpec con los defaults tomados del config actual.
        Cada subclase implementa su propio FormSpec.
        """
        raise NotImplementedError

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        """
        Aplica los valores devueltos por el backend al config y lo devuelve.
        Cada subclase implementa su propia lógica de mapeo.
        """
        raise NotImplementedError

    def run(self, config: GentlyConfig, backend: UIBackend) -> GentlyConfig:
        """
        Implementación por defecto: llama a build_form, pasa el FormSpec
        al backend, y aplica los valores con apply.
        Las subclases raramente necesitan sobreescribir este método.
        """
        form = self.build_form(config)
        values = backend.show_form(form)
        return self.apply(config, values)
```

### Motor de recolección en `gently.py`

```python
FORMS: list[SectionForm] = [
    SystemForm(),
    Stage3Form(),
    DisksForm(),
    PortageForm(),
    KernelForm(),
    BootloaderForm(),
    ServicesForm(),
    UsersForm(),
    PackagesForm(),
    DistccForm(),
]

def collect(config: GentlyConfig, backend: UIBackend) -> GentlyConfig:
    for form in FORMS:
        if form.is_complete(config):
            backend.show_info("✓", [f"{form.section_name} — configuración completa"])
        else:
            config = form.run(config, backend)
    return config
```

---

## Milestone 3 — Formularios por sección

**Objetivo**: implementar `is_complete`, `build_form` y `apply` para cada sección.

### Criterios de completitud por sección

| Sección | Campos obligatorios para `is_complete = True` |
|---|---|
| system | hostname, timezone, locale, keymap, lang |
| stage3 | arch, variant, y al menos uno de: local_path / tarball_url / mirror |
| disks | al menos un disco con device, partition_table, boot_mode, y al menos una partición con label, size, filesystem |
| portage | cflags, makeopts, use, accept_keywords, accept_license, profile.name |
| kernel | method |
| bootloader | type, y los campos obligatorios de la subsección correspondiente al type elegido |
| services | roles.network |
| users | contraseña de root por cualquiera de las tres vías: credentials_file, password, o password_hash |
| packages | siempre completo (la lista puede estar vacía) |
| distcc | si enabled=true: hosts no vacío. Si enabled=false o ausente: completo |

### Orden de implementación recomendado

De menor a mayor complejidad de formulario:

1. `PackagesForm` — lista simple
2. `KernelForm` — selección con nota de métodos no implementados en v1
3. `SystemForm` — campos de texto y selección simples
4. `Stage3Form` — lógica de completitud con tres fuentes alternativas
5. `BootloaderForm` — campos condicionales según el tipo elegido
6. `DistccForm` — sección completamente opcional
7. `ServicesForm` — selección de daemon por rol
8. `PortageForm` — make.conf global y repos
9. `UsersForm` — root + lista dinámica de cuentas de usuario
10. `DisksForm` — el más complejo: lista dinámica de discos y particiones

### Consideraciones especiales para DisksForm

`DisksForm` es el único que no puede modelarse como un formulario estático de
campos. Necesita una pantalla de gestión de listas con acciones: añadir disco,
editar disco, añadir partición, editar partición, eliminar. Para ello:

- `UIBackend` debe exponer un método adicional `show_list_manager` que gestione
  este patrón, o alternativamente `DisksForm.run` sobreescribe el comportamiento
  por defecto y orquesta múltiples llamadas al backend.
- La segunda opción es preferible para no complicar la interfaz abstracta.
- Al arrancar el formulario de discos, mostrar los discos detectados en el sistema
  con `lsblk` como referencia.
- En v1, si el usuario intenta añadir un segundo disco, mostrar un aviso de que
  multi-disco no está implementado y bloquear la acción.

---

## Milestone 4 — Resumen y confirmación

**Objetivo**: pantalla de revisión completa antes de tocar el disco.

### Comportamiento

- Se muestra siempre si algún disco tiene `confirm_wipe = true` o si el campo
  está ausente (se asume true por seguridad).
- El resumen agrupa los valores por sección con sus encabezados.
- El layout de disco se muestra de forma explícita: disco → particiones →
  filesystem → punto de montaje.
- Tres opciones: **Iniciar instalación** / **Editar sección** / **Guardar config y salir**.
- "Guardar config y salir" escribe el `config.toml` completo y termina sin
  haber tocado el disco. Permite relanzar Gently más tarde sin volver a los
  formularios.
- "Editar sección" vuelve al formulario de esa sección y después regresa
  al resumen.

---

## Milestone 5 — Ejecución desatendida

**Objetivo**: implementar las fases de instalación. El config está garantizadamente
completo y validado al inicio de esta fase.

El orquestador en `installer/runner.py` llama a cada fase en orden. Si una fase
falla, muestra el error con contexto completo y detiene la ejecución. No hay
reintentos automáticos en v1.

### Gestión de errores

Cada fase captura las excepciones de subprocess y las relanza como:

```python
class InstallPhaseError(Exception):
    phase: str
    command: str
    returncode: int
    stderr: str
```

El orquestador las captura y las pasa a `backend.show_error`.

### Gestión del chroot

La decisión sobre cómo gestionar el chroot (cuándo activarlo, cómo estructurar
los comandos dentro de él) se tomará en el momento de implementar las fases que
lo requieren. No se decide ahora.

### Fases en orden

**Fase 1 — Preflight** (`installer/preflight.py`)

Sin modificar nada en el sistema. Verificar:
- Conectividad: `ping -c 1 8.8.8.8`
- El dispositivo de disco existe y no está montado
- Espacio suficiente en `/mnt` para el stage3
- Si `local_path` está especificado: el fichero existe y es legible
- Si `distcc.enabled`: al menos un host es alcanzable por TCP en el puerto configurado
- Comandos necesarios disponibles: `parted`, `mkfs.ext4`, `mkfs.vfat`, `tar`, `gpg`

**Fase 2 — Particionado** (`installer/partition.py`)

- Crear tabla de particiones y particiones con `parted`.
- Formatear cada partición con el filesystem correspondiente.
- Montar las particiones en `/mnt/gentoo` en orden correcto (raíz primero,
  luego subdirectorios por profundidad de ruta).
- Activar swap si existe.
- Escribir `/mnt/gentoo/etc/fstab`.

**Fase 3 — Stage3** (`installer/stage3.py`)

Fuente por prioridad: `local_path` > `tarball_url` > descarga automática.

Para descarga automática: consultar
`https://distfiles.gentoo.org/releases/<arch>/autobuilds/latest-stage3-<arch>-<variant>.txt`,
construir la URL del tarball, descargar.

Si `verify_signature = true`: verificar la firma GPG antes de extraer.

Extracción: `tar xpvf <tarball> -C /mnt/gentoo --xattrs-include='*.*' --numeric-owner`.

**Fase 4 — Portage** (`installer/portage.py`)

- Escribir `/mnt/gentoo/etc/portage/make.conf`.
- Si `distcc.enabled`: añadir `distcc` a FEATURES y calcular MAKEOPTS.
- Copiar DNS del host: `cp /etc/resolv.conf /mnt/gentoo/etc/resolv.conf`.
- Crear `/mnt/gentoo/etc/portage/repos.conf/` con los repos configurados.
- Sync del árbol de Portage.
- Seleccionar perfil.
- Escribir ficheros de `package.use`, `package.accept_keywords`, `package.license`,
  `package.mask`, `package.unmask`, `package.env`.

**Fase 5 — Kernel** (`installer/kernel.py`)

- `method = "binary"`: `emerge sys-kernel/gentoo-kernel-bin`
- `method = "gentoo"`: `emerge sys-kernel/gentoo-kernel`
- Cualquier otro valor: error explícito indicando que no está implementado en v1.

**Fase 6 — Sistema base** (`installer/system.py`)

- Escribir `/mnt/gentoo/etc/conf.d/hostname`.
- Configurar timezone.
- Generar locales y establecer LANG.
- Configurar keymap.

**Fase 7 — Servicios** (`installer/services.py`)

- Para cada rol en `services.roles`: emerge el daemon elegido y lo activa
  (con `rc-update` en OpenRC, `systemctl enable` en systemd).
- Escribe la configuración de red según el manager y `services.network`.
- Procesa `services.extra.enable`.

**Fase 8 — Usuarios** (`installer/users.py`)

Contraseñas por prioridad: `credentials_file` > `password_hash` > `password`.

- Debe existir al menos un users.accounts para root
- Para cada cuenta: `useradd` con sus parámetros, asignar contraseña.

**Fase 9 — Bootloader** (`installer/bootloader.py`)

- `type = "grub"`:
  - `emerge sys-boot/grub`
  - `grub-install` con los parámetros de `bootloader.grub`
  - Escribir `/mnt/gentoo/etc/default/grub`
  - `grub-mkconfig -o /boot/grub/grub.cfg`
- `type = "systemd-boot"`: reservado para v2, error explícito en v1.

**Fase 10 — Paquetes adicionales** (`installer/packages.py`)

- `emerge` de todos los paquetes en `packages.extra`.
- Si `distcc.install_on_target = true` y `distcc.enabled = true`:
  `emerge sys-devel/distcc` y escribir `/etc/distcc/hosts`.

**Fase 11 — Cleanup** (`installer/runner.py`)

- Desmontar en orden inverso.
- Desactivar swap.
- Mostrar resumen: tiempo total por fase, estado de cada una.
- Informar al usuario de que puede reiniciar.

---

## Compatibilidad con versiones futuras

Los siguientes elementos deben estar presentes en v1 aunque no implementados,
emitiendo errores claros y descriptivos si se intentan usar:

- Métodos de kernel `menuconfig` y `custom`: error con mensaje
  `"Método '<method>' no implementado en v1. Usa 'binary' o 'gentoo'."`.
- `bootloader.type = "systemd-boot"`: error con mensaje equivalente.
- Más de un disco en `[[disks]]`: error con mensaje equivalente.
- Sección `[desktop]`: se parsea en el modelo pero se ignora en v1 emitiendo
  un aviso (no un error).
- Sección `[install_env]`: reservada para v2, se ignora en v1 emitiendo un aviso.

---

## Criterios de finalización por milestone

| Milestone | Criterio |
|---|---|
| 0 | `load_config("config.toml")` devuelve un objeto válido con un config parcial y con uno vacío. `save_config` produce un TOML que `load_config` puede volver a cargar obteniendo el mismo objeto. |
| 1 | Se puede mostrar un formulario de prueba con campos de todos los tipos, navegar entre ellos, editar valores y confirmar. Cambiar el backend no requiere modificar ningún formulario. |
| 2 | El motor recorre todas las secciones, salta las completas con un aviso visible, e invoca el formulario de las incompletas. |
| 3 | Con un config vacío, Gently pide todos los datos necesarios. Con el config de referencia completo, no muestra ningún formulario. |
| 4 | El resumen muestra correctamente todos los valores. "Guardar y salir" produce un config que permite relanzar Gently sin formularios. |
| 5 | En una VM con el config de referencia completo, la instalación produce un sistema Gentoo arrancable. |