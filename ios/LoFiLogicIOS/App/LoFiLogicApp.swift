import SwiftUI

@main
struct LoFiLogicApp: App {
    @StateObject private var store = RollStore()
    @StateObject private var catalog = PresetCatalog()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(store)
                .environmentObject(catalog)
        }
    }
}
