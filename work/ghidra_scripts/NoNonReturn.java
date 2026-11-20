// Pre-script for the stock 6.5 import:
//  * Ghidra wrongly infers the BES trace printf (0x0C13B4B8) as non-returning and truncates every
//    caller -> disable non-returning *discovery*;
//  * declare the functions that really never return (asserts, __stack_chk_fail), otherwise the
//    decompiler glues the next function onto every assert call.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
public class NoNonReturn extends GhidraScript {
    static final String[][] NORETURN = {
        {"0x00263B8C", "a2dp_assert_ramx"}, {"0x20063B8C", "a2dp_assert_ram"},
        {"0x0C13A968", "assert_flash"}, {"0x0C087A94", "stack_chk_fail"},
    };
    @Override
    public void run() throws Exception {
        setAnalysisOption(currentProgram, "Non-Returning Functions - Discovered", "false");
        println("Non-Returning Functions - Discovered: disabled");
        for (String[] e : NORETURN) {
            Address a = toAddr(e[0]);
            disassemble(a);
            Function f = getFunctionAt(a);
            if (f == null) f = createFunction(a, e[1]);
            if (f != null) { f.setNoReturn(true); println("noreturn " + e[1] + " @ " + a); }
            else println("could not create " + e[1] + " @ " + a);
        }
    }
}
