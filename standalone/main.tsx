// Standalone entry: mounts PluginHello the way the plugin host used to, with
// the host-supplied props filled in locally.
//
//   prj_path     where per-instance data is kept (calibration, plans):
//                <repo>/standalone/data, git-ignored
//   UI_path      the UI derives script/serial_ctrl.py from its directory,
//                so it points at a file in the repo root
//   api_*        host data channel -- not used by this UI, no-ops

import React from 'react';
import { createRoot } from 'react-dom/client';
import PluginHello from '../PluginHello';

const nodeRequire = (window as any).require;
const path = nodeRequire('path');
const fs = nodeRequire('fs');
const proc = nodeRequire('process');

const repoRoot: string = proc.env.XPLC_REPO || path.resolve('.');
const dataDir = path.join(repoRoot, 'standalone', 'data');
const instanceId = 'standalone';
fs.mkdirSync(path.join(dataDir, instanceId), { recursive: true });

const root = createRoot(document.getElementById('root')!);
root.render(
  <PluginHello
    prj_path={dataDir.replace(/\\/g, '/')}
    plugin_name="PackerX"
    instance_id={instanceId}
    lib_path={repoRoot.replace(/\\/g, '/')}
    UI_path={path.join(repoRoot, 'index.html').replace(/\\/g, '/')}
    api_sendData={async () => []}
    api_setDataChannel={async () => []}
  />
);
