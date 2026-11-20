import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;

public class DecompileAt extends GhidraScript {
    @Override
    public void run() throws Exception {
        DecompInterface ifc = new DecompInterface();
        ifc.toggleCCode(true);
        ifc.toggleSyntaxTree(true);
        ifc.openProgram(currentProgram);

        for (String arg : getScriptArgs()) {
            Address addr = toAddr(arg);
            Function f = getFunctionAt(addr);
            if (f == null) f = getFunctionContaining(addr);
            if (f == null) {
                // try to disassemble and create a function
                disassemble(addr);
                f = createFunction(addr, null);
            }
            if (f == null) {
                println("### no function at " + arg);
                continue;
            }
            println("\n===== " + f.getName() + " @ " + f.getEntryPoint() + " =====");
            DecompileResults res = ifc.decompileFunction(f, 120, monitor);
            if (res == null || !res.decompileCompleted()) {
                println("### decompile failed: " + (res == null ? "null" : res.getErrorMessage()));
                continue;
            }
            println(res.getDecompiledFunction().getC());
        }
        ifc.dispose();
    }
}
