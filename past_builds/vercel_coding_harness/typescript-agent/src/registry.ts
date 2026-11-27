// Tool registry: the one place the agent's tool set is assembled. Built-in
// tools register through registerBuiltins; a project adds its own tools with
// registry.register(...) or wraps a built-in with wrapTool, without editing
// the harness. The agent's `tools` and the prompt's tool list both come from
// the registry, so they always match.
import { tool, type Tool } from "ai";
import type { Sandbox } from "./sandbox";
import type { Skill } from "./skills";
import { createApproval, type ApprovalConfig } from "./approval";
import {
  createAskUserTool, createBashTool, createGrepTool, createLoadSkillTool,
  createReadTool, createTaskTool, createTodoTool, type SubagentListener,
} from "./tools";

export interface ToolRegistry {
  register(name: string, tool: Tool): void;
  get(name: string): Tool | undefined;
  list(): string[];
  entries(): [string, Tool][];
}

// A Map behind a small interface. It holds no policy: whoever calls
// register decides what gets in. Registering an existing name replaces it.
export function createRegistry(): ToolRegistry {
  const tools = new Map<string, Tool>();
  return {
    register: (name, t) => void tools.set(name, t),
    get: (name) => tools.get(name),
    list: () => [...tools.keys()],
    entries: () => [...tools.entries()],
  };
}

// Registers the built-in tools. Order matters: task reuses read and grep, so
// they must be registered first. approval sets bash's mode for this surface
// (the CLI uses the APPROVAL_MODE setting, the web server uses background
// because a human approves first); onSubagent is the optional listener for
// subagent events.
export function registerBuiltins(
  registry: ToolRegistry,
  sandbox: Sandbox,
  skills: Skill[],
  options: { approval: ApprovalConfig; onSubagent?: SubagentListener },
) {
  const read = createReadTool(sandbox);
  const grep = createGrepTool(sandbox);
  registry.register("read", read);
  registry.register("grep", grep);
  registry.register("bash", createBashTool(sandbox, createApproval(options.approval)));
  registry.register("task", createTaskTool(sandbox, { read, grep }, options.onSubagent));
  registry.register("askUser", createAskUserTool());
  registry.register("todo", createTodoTool());
  registry.register("loadSkill", createLoadSkillTool(skills));
}

interface WrapHooks {
  beforeExecute?: (input: any) => any | Promise<any>; // may rewrite the input
  afterExecute?: (result: any) => any | Promise<any>; // may rewrite the result
}

// Returns a new tool that runs the hooks around the base tool's execute. The
// base tool is unchanged, so other users of it are unaffected; register the
// wrapped tool under the same name to replace what the agent sees.
export function wrapTool(base: Tool, hooks: WrapHooks): Tool {
  return tool({
    description: base.description,
    inputSchema: base.inputSchema,
    execute: async (input: any, options: any) => {
      if (!base.execute) throw new Error("wrapTool needs a tool with an execute function");
      const transformed = hooks.beforeExecute ? await hooks.beforeExecute(input) : input;
      const result = await base.execute(transformed, options);
      return hooks.afterExecute ? await hooks.afterExecute(result) : result;
    },
  } as any);
}
