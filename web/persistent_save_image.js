import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { applyTextReplacements } from "../../scripts/utils.js";

const NODE_NAME = "PersistentSaveImage";
const GALLERY_WIDGET = "gallery_id";
const GALLERY_PROPERTY = "persistent_save_image_gallery_id";
const REFRESH_INTERVAL_MS = 1500;
const MIN_NODE_REFRESH_MS = 500;

function createGalleryId() {
    if (globalThis.crypto?.randomUUID) {
        return globalThis.crypto.randomUUID();
    }
    return `psi-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

function getGalleryWidget(node) {
    return node.widgets?.find((widget) => widget.name === GALLERY_WIDGET);
}

function setGalleryId(node, value) {
    const galleryId = String(value ?? "").trim();
    if (!galleryId) return "";

    const widget = getGalleryWidget(node);
    if (widget) widget.value = galleryId;

    node.properties ??= {};
    node.properties[GALLERY_PROPERTY] = galleryId;
    return galleryId;
}

function getGalleryId(node) {
    const widget = getGalleryWidget(node);
    if (!widget) return "";

    const widgetValue = String(widget.value ?? "").trim();
    if (widgetValue) return setGalleryId(node, widgetValue);

    const propertyValue = String(node.properties?.[GALLERY_PROPERTY] ?? "").trim();
    if (propertyValue) return setGalleryId(node, propertyValue);

    return setGalleryId(node, createGalleryId());
}

function hideGalleryWidget(node) {
    const widget = getGalleryWidget(node);
    if (!widget) return;

    widget.hidden = true;
    widget.type = "hidden";
    widget.computeSize = () => [0, -4];
}

function installFilenameFormatting(node) {
    const widget = node.widgets?.find((item) => item.name === "filename_prefix");
    if (!widget || widget.__persistentSaveImageFormatting) return;

    widget.__persistentSaveImageFormatting = true;
    widget.serializeValue = () => {
        try {
            return applyTextReplacements(app, String(widget.value ?? ""));
        } catch (error) {
            console.warn("Persistent Save Image: filename formatting fallback", error);
            return widget.value;
        }
    };
}

function isPersistentNode(node) {
    return node?.comfyClass === NODE_NAME || node?.type === NODE_NAME;
}

function activeGraphNodes() {
    const graph = app.rootGraph ?? app.graph;
    if (!graph) return [];
    return graph.nodes ?? graph._nodes ?? [];
}

function publishImages(node, images) {
    const output = { images: Array.isArray(images) ? images : [] };

    // Use the same event path as a real server execution. This updates both
    // the current Vue node renderer and the legacy LiteGraph image preview.
    try {
        api.dispatchEvent(
            new CustomEvent("executed", {
                detail: {
                    node: String(node.id),
                    display_node: String(node.id),
                    output,
                },
            }),
        );
        node.images = output.images;
        app.nodeOutputs[String(node.id)] = output;
        app.canvas?.setDirty(true, true);
    } catch (error) {
        console.warn("Persistent Save Image: executed event fallback", error);
        app.nodeOutputs[String(node.id)] = output;
        node.images = output.images;
        node.onExecuted?.(output);
        app.canvas?.setDirty(true, true);
    }
}

async function restoreGallery(node, force = false) {
    if (!isPersistentNode(node) || !node.graph) return;

    const now = Date.now();
    if (!force && now - (node.__persistentGalleryLastRefresh ?? 0) < MIN_NODE_REFRESH_MS) {
        return;
    }
    node.__persistentGalleryLastRefresh = now;

    const galleryId = getGalleryId(node);
    if (!galleryId) return;

    const requestVersion = node.__persistentGalleryExecutionVersion ?? 0;
    const requestId = (node.__persistentGalleryRequestId ?? 0) + 1;
    node.__persistentGalleryRequestId = requestId;

    try {
        const response = await api.fetchApi(
            `/persistent-save-image/gallery?gallery_id=${encodeURIComponent(galleryId)}`,
        );
        if (!response.ok) return;
        const data = await response.json();

        if (node.__persistentGalleryRequestId !== requestId) return;
        if ((node.__persistentGalleryExecutionVersion ?? 0) !== requestVersion) return;
        if (getGalleryId(node) !== galleryId) return;
        if (!Array.isArray(data.images)) return;

        publishImages(node, data.images);
    } catch (error) {
        console.warn("Persistent Save Image: could not restore gallery", error);
    }
}

async function resetGallery(node) {
    const galleryId = getGalleryId(node);
    if (!galleryId) return;

    try {
        const response = await api.fetchApi("/persistent-save-image/reset", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ gallery_id: galleryId }),
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        node.__persistentGalleryExecutionVersion =
            (node.__persistentGalleryExecutionVersion ?? 0) + 1;
        publishImages(node, []);
        node.images = [];
        node.imgs = [];
        node.imageIndex = null;
        app.canvas?.setDirty(true, true);
    } catch (error) {
        console.error("Persistent Save Image: reset failed", error);
        globalThis.alert?.("Não foi possível resetar a galeria do Persistent Save Image.");
    }
}

function initialiseNode(node) {
    if (node.__persistentSaveImageInitialised) return;
    node.__persistentSaveImageInitialised = true;

    getGalleryId(node);
    hideGalleryWidget(node);
    installFilenameFormatting(node);

    node.addWidget("button", "Reset Galleria", null, () => resetGallery(node), {
        serialize: false,
    });

    const originalOnExecuted = node.onExecuted;
    node.onExecuted = function (message) {
        this.__persistentGalleryExecutionVersion =
            (this.__persistentGalleryExecutionVersion ?? 0) + 1;
        return originalOnExecuted?.apply(this, arguments);
    };

    queueMicrotask(() => restoreGallery(node, true));
}

function refreshActiveGraph(force = false) {
    for (const node of activeGraphNodes()) {
        if (isPersistentNode(node)) {
            initialiseNode(node);
            restoreGallery(node, force);
        }
    }
}

let lastGraph = null;

app.registerExtension({
    name: "PersistentSaveImage.Gallery",

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalOnNodeCreated?.apply(this, arguments);
            initialiseNode(this);
            return result;
        };

        const originalOnSerialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (data) {
            const result = originalOnSerialize?.apply(this, arguments);
            const galleryId = getGalleryId(this);
            data.properties ??= {};
            data.properties[GALLERY_PROPERTY] = galleryId;
            return result;
        };

        const originalOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (data) {
            // Read the redundant node property before normal configuration.
            // This survives workflow embedding in PNG metadata more reliably
            // than depending on a presentation-hidden widget alone.
            const configuredGalleryId = String(
                data?.properties?.[GALLERY_PROPERTY] ?? "",
            ).trim();

            const result = originalOnConfigure?.apply(this, arguments);
            if (configuredGalleryId) {
                setGalleryId(this, configuredGalleryId);
            } else {
                getGalleryId(this);
            }
            hideGalleryWidget(this);
            queueMicrotask(() => restoreGallery(this, true));
            return result;
        };
    },

    setup() {
        lastGraph = app.rootGraph ?? app.graph;
        refreshActiveGraph(true);

        globalThis.setInterval(() => {
            const graph = app.rootGraph ?? app.graph;
            if (graph !== lastGraph) {
                lastGraph = graph;
                refreshActiveGraph(true);
            }
        }, REFRESH_INTERVAL_MS);

        globalThis.addEventListener("focus", () => refreshActiveGraph(true));
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) refreshActiveGraph(true);
        });
    },
});
