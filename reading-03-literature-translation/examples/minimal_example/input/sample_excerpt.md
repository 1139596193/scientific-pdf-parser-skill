# Adaptive Thermal Control in Microfluidic Catalytic Reactors

Microfluidic catalytic reactors (MCRs) enable rapid heat and mass transfer during exothermic chemical synthesis [1], [2]. However, localized thermal runaway remains a persistent bottleneck when catalyst loading exceeds standard thresholds. In this study, we introduce an adaptive proportional-integral controller that stabilizes the reactor core at 350 K within a response window of 0.25 ms. Unlike conventional passive cooling loops [3], our feedback scheme does not require external phase-change reservoirs and reduces peak temperature overshoot by 18.4% across continuous flow trials. As illustrated in Fig. 1 and governed by Eq. (1), the temperature deviation remains strictly bounded, with steady-state oscillation amplitudes at most 0.4 K under variable feed velocities.

Figure 1. Schematic of the microfluidic catalytic reactor (MCR) thermal feedback loop operating at 350 K with dual optical sensors.

$$\Delta T_{\max} \le \frac{Q_{\mathrm{in}}}{C_p \dot{m}} \quad (1)$$
