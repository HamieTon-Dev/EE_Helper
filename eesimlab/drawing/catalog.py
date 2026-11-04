"""Component catalog for the schematic editor.

Families are expanded over standard value series (E24 resistors, E12 capacitors and
inductors, voltage/power ratings), giving thousands of placeable parts. Each entry says
whether the solver simulates it (R, L, C, independent sources, switches) or whether it is
a drawing-only symbol, which analysis will clearly report as unsupported.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from eesimlab.eecalc.units import fmt

E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]
E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]


@dataclass
class Part:
    key: str
    name: str
    category: str
    kind: str  # model kind (R C L V I SW, or a drawing-only kind)
    symbol: str  # drawing symbol id
    pins: list[str] = field(default_factory=lambda: ["1", "2"])
    value: float | None = None
    value_text: str = ""
    params: dict = field(default_factory=dict)  # polarized, rating_*, src, state
    simulated: bool = True
    description: str = ""

    def search_text(self) -> str:
        return f"{self.name} {self.category} {self.kind} {self.value_text} {self.description} {self.key}".lower()


def short(v: float, qty: str) -> str:
    """Compact value label, e.g. 4.7kΩ, 100nF, 2.2mH."""
    t = fmt(v, qty, 3).replace(" ", "")
    num = "".join(ch for ch in t if ch in "0123456789.-")
    unit = t[len(num):]
    if "." in num:
        num = num.rstrip("0").rstrip(".")
    return num + unit


def _series(series, decades):
    for d in decades:
        for m in series:
            yield float(f"{m * 10 ** d:.6g}")


def build_catalog() -> list[Part]:
    P: list[Part] = []
    add = P.append
    # ---------------------------------------------------------------- passive
    for watts, lab in ((0.125, "1/8 W"), (0.25, "1/4 W"), (0.5, "1/2 W"), (1.0, "1 W"), (2.0, "2 W"), (5.0, "5 W")):
        for v in _series(E24, range(0, 7)):
            if v > 10e6:
                continue
            t = short(v, "resistance")
            add(Part(f"R-{v:g}-{watts:g}W", f"Resistor {t} {lab}", "Resistors", "R", "R", value=v, value_text=t,
                     params={"rating_power": watts}, description="metal film, 1 %"))
    for v in (100, 500, 1e3, 2e3, 5e3, 10e3, 20e3, 50e3, 100e3, 500e3, 1e6):
        t = short(v, "resistance")
        add(Part(f"POT-{v:g}", f"Potentiometer {t} (set as fixed R)", "Resistors", "R", "POT", value=v / 2,
                 value_text=f"{t}/2", params={"rating_power": 0.5}, description="wiper at mid-travel; edit R for other positions"))
    for v in _series(E12, range(-12, -5)):  # 1 pF … 820 nF ceramic/film
        t = short(v, "capacitance")
        add(Part(f"C-{v:g}", f"Capacitor {t} ceramic/film", "Capacitors", "C", "C", value=v, value_text=t,
                 params={"rating_voltage": 50.0}))
    for vr in (6.3, 10, 16, 25, 35, 50, 63, 100, 250, 450):
        for v in _series(E12[::2], range(-7, -1)):  # 100 nF … 6.8 mF electrolytic
            t = short(v, "capacitance")
            add(Part(f"CE-{v:g}-{vr:g}V", f"Electrolytic {t} {vr:g} V", "Capacitors (polarised)", "C", "CPOL", value=v,
                     value_text=t, params={"polarized": True, "rating_voltage": float(vr)}, description="pin 1 = +"))
    for v in _series(E12, range(-9, 1)):  # 1 nH … 8.2 H
        t = short(v, "inductance")
        add(Part(f"L-{v:g}", f"Inductor {t}", "Inductors", "L", "L", value=v, value_text=t))
    # ---------------------------------------------------------------- sources
    for v in (1.2, 1.5, 3.0, 3.3, 3.7, 5.0, 6.0, 9.0, 12.0, 15.0, 18.0, 24.0, 48.0):
        add(Part(f"VDC-{v:g}", f"DC voltage source {v:g} V", "Sources", "V", "VDC",
                 params={"src": {"wave": "dc", "dc": v}}, description="pin 1 = +"))
        add(Part(f"BAT-{v:g}", f"Battery {v:g} V", "Sources", "V", "BAT", params={"src": {"wave": "dc", "dc": v}},
                 description="ideal cell; pin 1 = +"))
    for vrms, f in ((120, 60), (230, 50), (240, 50), (12, 60), (24, 60), (1, 1000), (5, 1000), (1, 50)):
        add(Part(f"VAC-{vrms:g}-{f:g}", f"AC source {vrms:g} V RMS {f:g} Hz", "Sources", "V", "VAC",
                 params={"src": {"wave": "sine", "amp": round(vrms * 2 ** 0.5, 6), "freq": float(f), "offset": 0.0}},
                 description="sine; amplitude stored as peak"))
    add(Part("VSIN", "Function generator: sine", "Sources", "V", "VAC", params={"src": {"wave": "sine", "amp": 1.0, "freq": 1000.0}}))
    add(Part("VSQ", "Function generator: square", "Sources", "V", "VSQ", params={"src": {"wave": "square", "amp": 1.0, "freq": 1000.0}}))
    add(Part("VSTEP", "Step source (0 → V at t = 0)", "Sources", "V", "VDC", params={"src": {"wave": "step", "dc": 5.0}}))
    for i in (1e-6, 1e-3, 10e-3, 0.1, 1.0):
        add(Part(f"IDC-{i:g}", f"DC current source {fmt(i, 'current', 0)}", "Sources", "I", "IDC",
                 params={"src": {"wave": "dc", "dc": i}}, description="current flows pin 1 → pin 2 through the source"))
    add(Part("GND", "Ground (reference)", "Sources", "GND", "GND", pins=["1"], description="marks the node as 0 V"))
    # ---------------------------------------------------------------- switches / electromechanical
    add(Part("SW-SPST-O", "Switch SPST (open)", "Switches & relays", "SW", "SW", params={"state": "open"}))
    add(Part("SW-SPST-C", "Switch SPST (closed)", "Switches & relays", "SW", "SW", params={"state": "closed"}))
    add(Part("SW-PB-NO", "Push button NO (released = open)", "Switches & relays", "SW", "PB", params={"state": "open"}))
    add(Part("SW-PB-NC", "Push button NC (released = closed)", "Switches & relays", "SW", "PB", params={"state": "closed"}))
    for v in (5, 12, 24, 48):
        add(Part(f"RLY-COIL-{v}", f"Relay coil {v} V (as R + L)", "Switches & relays", "U", "RELAY", pins=["A1", "A2"],
                 simulated=False, description="draw the coil; simulate it as Rcoil + Lcoil and the contact as a switch"))
        add(Part(f"RLY-SPDT-{v}", f"Relay SPDT {v} V (5 pins)", "Switches & relays", "U", "RELAY5",
                 pins=["A1", "A2", "COM", "NO", "NC"], simulated=False))
    for a in (0.5, 1, 2, 5, 10, 20):
        add(Part(f"FUSE-{a}", f"Fuse {a} A", "Protection", "F", "FUSE", simulated=False,
                 description="model as a closed switch with its cold resistance if needed"))
    for w in (1, 5, 25, 60, 100):
        add(Part(f"LAMP-{w}", f"Lamp {w} W", "Loads", "LAMP", "LAMP", simulated=False, description="model as a resistor V²/P"))
    add(Part("MOTOR-DC", "DC motor", "Loads", "MOTOR", "MOTOR", simulated=False))
    add(Part("SPK-8", "Speaker 8 Ω", "Loads", "R", "SPK", value=8.0, value_text="8Ω", description="resistive approximation"))
    for ratio in ("1:1", "2:1", "5:1", "10:1", "20:1", "1:10"):
        add(Part(f"XFMR-{ratio}", f"Transformer {ratio}", "Transformers", "T", "XFMR", pins=["P1", "P2", "S1", "S2"],
                 simulated=False, description="use the Transformer calculator for winding analysis"))
    # ---------------------------------------------------------------- semiconductors (drawing only)
    for n in ("1N4148", "1N4001", "1N4004", "1N4007", "1N5819", "1N5822", "BAT54", "UF4007", "FR107"):
        add(Part(f"D-{n}", f"Diode {n}", "Diodes", "D", "D", simulated=False, description="anode = pin 1"))
    for vz in (2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1, 10, 11, 12, 13, 15, 16, 18, 20,
               22, 24, 27, 30, 33, 36, 39, 43, 47):
        add(Part(f"DZ-{vz:g}", f"Zener diode {vz:g} V", "Diodes", "D", "DZ", simulated=False))
    for col in ("red", "green", "blue", "yellow", "white", "orange", "IR", "UV"):
        add(Part(f"LED-{col}", f"LED {col}", "Diodes", "LED", "LED", simulated=False))
    for n, t in (("2N2222", "NPN"), ("2N3904", "NPN"), ("2N3906", "PNP"), ("BC547", "NPN"), ("BC557", "PNP"), ("TIP31", "NPN"),
                 ("TIP32", "PNP"), ("TIP120", "NPN Darlington"), ("2N3055", "NPN power"), ("MJE13005", "NPN HV")):
        add(Part(f"Q-{n}", f"BJT {n} ({t})", "Transistors", "Q", "QNPN" if "NPN" in t else "QPNP", pins=["C", "B", "E"],
                 simulated=False))
    for n, t in (("2N7000", "N"), ("BS170", "N"), ("IRF540", "N"), ("IRFZ44N", "N"), ("IRLZ44N", "N logic"), ("IRF9540", "P"),
                 ("AO3401", "P"), ("IRF3205", "N")):
        add(Part(f"M-{n}", f"MOSFET {n} ({t}-channel)", "Transistors", "M", "MOSN" if t.startswith("N") else "MOSP",
                 pins=["D", "G", "S"], simulated=False))
    for n in ("LM741", "TL071", "TL072", "LM358", "LM324", "NE5532", "OPA2134", "LM311 (comparator)"):
        add(Part(f"U-{n}", f"Op-amp {n}", "Integrated circuits", "U", "OPAMP", pins=["IN+", "IN−", "OUT", "V+", "V−"],
                 simulated=False))
    for n, d in (("7805", "+5 V regulator"), ("7812", "+12 V regulator"), ("7905", "−5 V regulator"), ("LM317", "adjustable regulator"),
                 ("AMS1117-3.3", "3.3 V LDO"), ("NE555", "timer"), ("LM386", "audio amplifier"), ("ULN2003", "driver array"),
                 ("ATmega328P", "microcontroller"), ("ESP32", "microcontroller")):
        add(Part(f"U-{n}", f"{n} {d}", "Integrated circuits", "U", "IC", pins=["1", "2", "3", "4"], simulated=False))
    for g in ("AND", "OR", "NOT", "NAND", "NOR", "XOR", "XNOR", "BUFFER"):
        add(Part(f"G-{g}", f"Logic gate {g}", "Digital logic", "U", f"G_{g}", pins=["A", "Y"] if g in ("NOT", "BUFFER") else ["A", "B", "Y"],
                 simulated=False))
    for n in ("74HC00", "74HC02", "74HC04", "74HC08", "74HC32", "74HC86", "74HC14", "74HC74", "74HC595", "CD4017"):
        add(Part(f"U-{n}", f"{n} logic IC", "Digital logic", "U", "IC", pins=["1", "2", "3", "4"], simulated=False))
    for mhz in (4, 8, 12, 16, 20, 32.768e-3):
        add(Part(f"XTAL-{mhz:g}", f"Crystal {mhz:g} MHz", "Passive (other)", "X", "XTAL", simulated=False))
    for n in ("Terminal block 2-pin", "Header 2-pin", "Test point", "Jumper"):
        add(Part(f"CON-{n}", n, "Connectors", "X", "CONN", simulated=False))
    return P


CATALOG = build_catalog()
CATEGORIES = sorted({p.category for p in CATALOG}, key=lambda c: (c not in ("Resistors", "Capacitors", "Inductors", "Sources",
                                                                           "Switches & relays"), c))


def search(text: str, category: str | None = None, limit: int = 500) -> list[Part]:
    words = text.lower().split()
    out = []
    for p in CATALOG:
        if category and p.category != category:
            continue
        st = p.search_text()
        if all(w in st for w in words):
            out.append(p)
            if len(out) >= limit:
                break
    return out


def get(key: str) -> Part:
    for p in CATALOG:
        if p.key == key:
            return p
    raise KeyError(key)
