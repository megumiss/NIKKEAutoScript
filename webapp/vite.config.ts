import {defineConfig} from "vite";
import vue from "@vitejs/plugin-vue";
// @ts-ignore
import path from "path";

export default defineConfig({
    base: "./", // * 打包相对路径,否则electron加载index.html时找不到css,js文件
    plugins: [
        vue(),
    ],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "./src") // 路径别名
        }
    },
    build: {
        outDir: "output/dist" // 输出需要打包的文件
    }
});
