import sys

# Clean up cached submodules of BADR on reload/load so that all file changes take effect
for mod in list(sys.modules.keys()):
    if mod.startswith("BADR."):
        del sys.modules[mod]

def classFactory(iface):
    from .plugin import BADRPlugin
    return BADRPlugin(iface)

