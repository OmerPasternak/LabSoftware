"""Independent HHG analysis application and preserved MATLAB workflows."""


def __getattr__(name):
    if name in {"FundamentalAnalysisWindow", "main"}:
        from .window import FundamentalAnalysisWindow, main
        return {"FundamentalAnalysisWindow": FundamentalAnalysisWindow, "main": main}[name]
    raise AttributeError(name)
