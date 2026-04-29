module.exports = {
    flowFile: 'flows.json',
    userDir: '/data',
    functionGlobalContext: {
        // os:require('os'),
    },
    exportGlobalContextKeys: false,
    logging: {
        console: {
            level: "info",
            metrics: false,
            audit: false
        }
    },
    editorTheme: {
        projects: {
            enabled: false
        }
    }
}
