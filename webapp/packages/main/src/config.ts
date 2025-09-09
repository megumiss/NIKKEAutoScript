const yaml = require('yaml');
const fs = require('fs');
const path = require('path');

// export const nkasPath = 'D:/AzurLaneAutoScript';
export const nkasPath = process.cwd();

const file = fs.readFileSync(path.join(nkasPath, './config/deploy.yaml'), 'utf8');
const config = yaml.parse(file);
const PythonExecutable = config.Deploy.Python.PythonExecutable;
const WebuiPort = config.Deploy.Webui.WebuiPort.toString();

export const pythonPath = (path.isAbsolute(PythonExecutable) ? PythonExecutable : path.join(nkasPath, PythonExecutable));
export const webuiUrl = `http://127.0.0.1:${WebuiPort}`;
export const webuiPath = 'gui.py';
export const webuiArgs = ['--port', WebuiPort, '--electron'];
export const dpiScaling = Boolean(config.Deploy.Webui.DpiScaling) || (config.Deploy.Webui.DpiScaling === undefined) ;
