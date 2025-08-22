const yaml = require('yaml');
const fs = require('fs');
const path = require('path');

export const nkasPath = path.join(process.cwd(), '../')
const file = fs.readFileSync(path.join(nkasPath, './config/deploy.yaml'), 'utf8');
const config = yaml.parse(file);
const PythonExecutable = config.Deploy.Python.PythonExecutable;

export const pythonPath = (path.isAbsolute(PythonExecutable) ? PythonExecutable : path.join(nkasPath, PythonExecutable));

const WebuiPort = config.Deploy.Webui.WebuiPort.toString();

export const webuiPath = path.join(nkasPath)
export const webuiArgs = ['--port', WebuiPort];
export const webuiUrl = `http://127.0.0.1:${WebuiPort}`;

