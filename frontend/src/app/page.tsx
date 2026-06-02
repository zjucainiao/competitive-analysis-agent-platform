import { redirect } from "next/navigation";

/**
 * 根路径直接走 projects 列表（产品自然入口）。
 *
 * 设计系统参考页移到 `/design-system`。
 * 演示直链 `/projects/demo/runs/01`。
 */
export default function HomePage() {
  redirect("/projects");
}
