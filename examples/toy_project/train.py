import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--lr", type=float, default=1e-3)
ap.add_argument("--drop_path", type=float, default=0.0)
ap.add_argument("--amp", action="store_true")
ap.add_argument("--loss", choices=["asl", "bce"], default="bce")
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--head", default="")
args = ap.parse_args()
print("run", args)
