use web_sys::CanvasRenderingContext2d;

const NODE_NAMES: &[&str] = &[
    "auth", "jwt", "rate-limit", "schema", "migration", "webhook", "cache",
    "cloudtrail", "iam", "kafka", "elastic", "lambda", "s3", "redis", "eslint",
    "rust", "tokio", "axum", "sqlite", "tantivy", "leptos", "tauri", "vpc",
    "rds", "ci/cd",
];
const NODE_COUNT: usize = 80;
const CASCADE_INTERVAL_MS: f64 = 7000.0;

// ─── Simple hash-based value noise (deterministic) ───
fn hn(x: f64, y: f64) -> f64 {
    let h = (x as i32).wrapping_mul(374761393) + (y as i32).wrapping_mul(668265263);
    let h = (h ^ (h >> 13)).wrapping_mul(1274126177);
    ((h ^ (h >> 16)) as f64) / 2147483647.0
}

fn sn(x: f64, y: f64, s: f64) -> f64 {
    let sx = x * s;
    let sy = y * s;
    let ix = sx.floor();
    let iy = sy.floor();
    let fx = sx - ix;
    let fy = sy - iy;
    let ux = fx * fx * (3.0 - 2.0 * fx);
    let uy = fy * fy * (3.0 - 2.0 * fy);
    let a = hn(ix, iy);
    let b = hn(ix + 1.0, iy);
    let c = hn(ix, iy + 1.0);
    let d = hn(ix + 1.0, iy + 1.0);
    a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy
}

// ─── Types ───
#[derive(Clone)]
pub struct Connection {
    pub target: usize,
    pub strength: f64,
}

#[derive(Clone)]
pub struct Node {
    pub id: usize,
    pub name: &'static str,
    pub x: f64,
    pub y: f64,
    pub r: f64,
    pub act: f64,
    pub connections: Vec<Connection>,
    pub glow: f64,
}

#[derive(Clone)]
pub struct TrailPoint {
    pub x: f64,
    pub y: f64,
    pub alpha: f64,
}

#[derive(Clone)]
pub struct Bead {
    pub from: usize,
    pub to: usize,
    pub pos: f64,
    pub speed: f64,
    pub intensity: f64,
    pub trail: Vec<TrailPoint>,
    pub visited: Vec<usize>,
    #[allow(dead_code)]
    pub branch_delay: f64,
}

struct SoilParticle {
    x: f64,
    y: f64,
    r: f64,
    a: f64,
}

fn bezier_pos(a: &Node, b: &Node, t: f64) -> (f64, f64) {
    let mx = (a.x + b.x) / 2.0;
    let my = (a.y + b.y) / 2.0;
    let drift = (a.x - b.x) * 0.12 + sn(t * 0.3 + a.id as f64 * 0.1, 0.0, 0.5) * 12.0;
    let cp_y = my + drift;
    let mt = 1.0 - t;
    let x = mt * mt * a.x + 2.0 * mt * t * mx + t * t * b.x;
    let y = mt * mt * a.y + 2.0 * mt * t * cp_y + t * t * b.y;
    (x, y)
}

/// Convert rgba components to rgba(...) string
fn rgba(r: u8, g: u8, b: u8, a: f64) -> String {
    format!("rgba({},{},{},{})", r, g, b, a)
}

// ─── Renderer ───
pub struct CanvasRenderer {
    pub nodes: Vec<Node>,
    beads: Vec<Bead>,
    soil: Vec<SoilParticle>,
    pub width: f64,
    pub height: f64,
    cx: f64,
    cy: f64,
    sy: f64,
    mouse_x: f64,
    mouse_y: f64,
    cascade_timer: f64,
    frame_count: u64,
    stream_y: f64,
}

impl CanvasRenderer {
    pub fn new(w: f64, h: f64) -> Self {
        let cx = w / 2.0;
        let cy = h / 2.0;
        let sy = h * 0.50;

        let mut nodes = Vec::with_capacity(NODE_COUNT);
        for i in 0..NODE_COUNT {
            nodes.push(Node {
                id: i,
                name: NODE_NAMES[i % NODE_NAMES.len()],
                x: js_sys::Math::random() * w,
                y: sy + (js_sys::Math::random() - 0.5) * h * 0.4,
                r: 3.5 + (i % 4) as f64 * 1.2,
                act: 0.2 + js_sys::Math::random() * 0.4,
                connections: Vec::new(),
                glow: 0.0,
            });
        }

        build_connections(&mut nodes);

        let soil: Vec<SoilParticle> = (0..80)
            .map(|_| SoilParticle {
                x: js_sys::Math::random(),
                y: js_sys::Math::random(),
                r: 0.5 + js_sys::Math::random() * 1.5,
                a: 0.04 + js_sys::Math::random() * 0.08,
            })
            .collect();

        CanvasRenderer {
            nodes,
            beads: Vec::new(),
            soil,
            width: w,
            height: h,
            cx,
            cy,
            sy,
            mouse_x: w / 2.0,
            mouse_y: h / 2.0,
            cascade_timer: 0.0,
            frame_count: 0,
            stream_y: sy,
        }
    }

    pub fn resize(&mut self, w: f64, h: f64) {
        self.width = w;
        self.height = h;
        self.cx = w / 2.0;
        self.cy = h / 2.0;
        self.sy = h * 0.50;
    }

    pub fn mouse_move(&mut self, x: f64, y: f64) {
        self.mouse_x = x;
        self.mouse_y = y;
    }

    #[allow(dead_code)]
    pub fn trigger_cascade_from(&mut self, node_id: usize) {
        if node_id >= self.nodes.len() {
            return;
        }
        let visited = vec![self.nodes[node_id].id];
        self.spawn_bead_from(node_id, &visited);
    }

    fn spawn_bead_from(&mut self, start_idx: usize, visited: &[usize]) {
        if start_idx >= self.nodes.len() {
            return;
        }
        let has_connections = !self.nodes[start_idx].connections.is_empty();
        if !has_connections {
            return;
        }
        // Collect connection targets while not holding a mutable borrow on self
        let targets: Vec<usize> = {
            let node = &self.nodes[start_idx];
            let mut sorted: Vec<&Connection> = node.connections.iter().collect();
            sorted.sort_by(|a, b| b.strength.partial_cmp(&a.strength).unwrap_or(std::cmp::Ordering::Equal));
            let count = 1.min(sorted.len());
            sorted.iter().take(count).map(|c| c.target).collect()
        };
        for target in targets {
            self.spawn_bead(start_idx, target, visited);
        }
    }

    fn spawn_bead(&mut self, source: usize, target: usize, visited: &[usize]) {
        let speed = 0.003 + js_sys::Math::random() * 0.002;
        self.beads.push(Bead {
            from: source,
            to: target,
            pos: 0.0,
            speed,
            intensity: 0.7 + js_sys::Math::random() * 0.3,
            trail: Vec::new(),
            visited: visited.to_vec(),
            branch_delay: 0.0,
        });
    }

    fn update_beads(&mut self, dt: f64, _t: f64) {
        let mut i = self.beads.len();

        while i > 0 {
            i -= 1;
            let b = &mut self.beads[i];
            b.pos += b.speed * dt;

            // Trail: store previous position
            let (px, py) = bezier_pos(&self.nodes[b.from], &self.nodes[b.to], b.pos);
            b.trail.push(TrailPoint { x: px, y: py, alpha: 1.0 });
            if b.trail.len() > 20 {
                b.trail.remove(0);
            }

            // Fade trail alpha
            for t_idx in (0..b.trail.len()).rev() {
                b.trail[t_idx].alpha -= 0.04;
                if b.trail[t_idx].alpha <= 0.0 {
                    b.trail.remove(t_idx);
                }
            }

            // Additional fade on older trail points
            for t_idx in (0..b.trail.len()).rev() {
                b.trail[t_idx].alpha *= 0.98;
                if b.trail[t_idx].alpha < 0.01 {
                    b.trail.remove(t_idx);
                }
            }

            // Reach destination?
            if b.pos >= 1.0 {
                // Flash the junction node
                if b.to < self.nodes.len() {
                    self.nodes[b.to].glow = 1.0;
                }

                // Branch: spawn new beads from target
                let to_idx = b.to;
                let visited_clone = b.visited.clone();
                let speed_in = b.speed;
                let intensity_in = b.intensity;

                if to_idx < self.nodes.len() {
                    let to_node = &self.nodes[to_idx];
                    let mut sorted: Vec<&Connection> = to_node.connections.iter().collect();
                    sorted.sort_by(|a, bb| bb.strength.partial_cmp(&a.strength).unwrap_or(std::cmp::Ordering::Equal));
                    let branch_count = 1.min(2.min(sorted.len()));
                    let mut spawned = 0;
                    for conn in &sorted {
                        if !visited_clone.contains(&conn.target) && spawned < branch_count {
                            let mut new_visited = visited_clone.clone();
                            new_visited.push(to_idx);
                            let speed_mod = 0.8 + js_sys::Math::random() * 0.4;
                            self.beads.push(Bead {
                                from: to_idx,
                                to: conn.target,
                                pos: 0.0,
                                speed: speed_in * speed_mod,
                                intensity: intensity_in * 0.85,
                                trail: vec![TrailPoint { x: to_node.x, y: to_node.y, alpha: 0.6 }],
                                visited: new_visited,
                                branch_delay: 0.0,
                            });
                            spawned += 1;
                        }
                    }
                }

                self.beads.remove(i);
            }
        }

        // Limit total beads
        if self.beads.len() > 30 {
            self.beads.sort_by(|a, b| b.pos.partial_cmp(&a.pos).unwrap_or(std::cmp::Ordering::Equal));
            self.beads.truncate(20);
        }
    }

    pub fn render(&mut self, ctx: &CanvasRenderingContext2d, _now: f64, dt: f64) {
        self.frame_count += 1;
        let t = _now * 0.001;

        let dt_frame = if dt > 0.0 { dt } else { 16.7 };

        // ── Cascade scheduling ──
        self.cascade_timer += dt_frame;
        if self.cascade_timer > CASCADE_INTERVAL_MS && self.beads.is_empty() {
            // Pick random start node
            let start_idx = (js_sys::Math::random() * self.nodes.len() as f64) as usize;
            let visited = vec![self.nodes[start_idx].id];
            self.spawn_bead_from(start_idx, &visited);
            self.cascade_timer = 0.0;
        }

        // ── Update ──
        self.update_beads(dt_frame, t);

        // Node glow decay
        for n in &mut self.nodes {
            n.glow *= 0.97;
        }

        // Node drift
        let mx_ratio = self.mouse_x / self.width;
        let my_ratio = self.mouse_y / self.height;
        for n in &mut self.nodes {
            n.x += sn(t + n.id as f64, 0.0, 0.2) * 0.04;
            n.y += sn(t, n.id as f64 * 0.3, 0.2) * 0.04;
            n.x += (mx_ratio - 0.5) * 0.12 * (0.3 + n.glow * 0.5);
            n.y += (my_ratio - 0.5) * 0.06 * (0.3 + n.glow * 0.5);
            let dy = n.y - self.stream_y;
            if dy > self.height * 0.3 {
                n.y -= 0.03;
            }
            if dy < -self.height * 0.3 {
                n.y += 0.03;
            }
        }

        // ═══════════════════════════════════
        //  DRAW
        // ═══════════════════════════════════

        ctx.clear_rect(0.0, 0.0, self.width, self.height);

        // ── Background: radial gradient (warm earth) ──
        let bg = ctx
            .create_radial_gradient(self.cx, self.cy, 0.0, self.cx, self.cy, self.height * 0.9)
            .unwrap();
        bg.add_color_stop(0.0, "#1e1814").unwrap();
        bg.add_color_stop(0.3, "#16120e").unwrap();
        bg.add_color_stop(0.6, "#100c0a").unwrap();
        bg.add_color_stop(1.0, "#0a0806").unwrap();
        ctx.set_fill_style_canvas_gradient(&bg);
        ctx.fill_rect(0.0, 0.0, self.width, self.height);

        // ── Soil particles ──
        for s in &self.soil {
            ctx.begin_path();
            let _ = ctx.arc(s.x * self.width, s.y * self.height, s.r, 0.0, std::f64::consts::PI * 2.0);
            ctx.set_fill_style_str(&rgba(60, 40, 30, s.a));
            ctx.fill();
        }

        // ── Stream halo ──
        let sh = ctx
            .create_radial_gradient(self.cx, self.stream_y, 0.0, self.cx, self.stream_y, self.height * 0.3)
            .unwrap();
        sh.add_color_stop(0.0, "rgba(60,30,20,0.10)").unwrap();
        sh.add_color_stop(0.5, "rgba(40,20,15,0.05)").unwrap();
        sh.add_color_stop(1.0, "transparent").unwrap();
        ctx.set_fill_style_canvas_gradient(&sh);
        ctx.fill_rect(0.0, self.stream_y - self.height * 0.3, self.width, self.height * 0.6);

        // ── Ambient purple ──
        let amb_x = self.cx + (t * 0.1).sin() * self.width * 0.08;
        let amb_y = self.stream_y + (t * 0.08).cos() * self.height * 0.03;
        let ag = ctx
            .create_radial_gradient(amb_x, amb_y, 0.0, amb_x, amb_y, self.height * 0.2)
            .unwrap();
        ag.add_color_stop(0.0, "rgba(80,40,160,0.03)").unwrap();
        ag.add_color_stop(1.0, "transparent").unwrap();
        ctx.set_fill_style_canvas_gradient(&ag);
        ctx.fill_rect(0.0, 0.0, self.width, self.height);

        // ── Draw all edges (veins) ──
        ctx.set_line_cap("round");
        ctx.set_shadow_blur(0.0);
        for n in &self.nodes {
            for c in &n.connections {
                if c.target <= n.id {
                    continue;
                }
                let other = &self.nodes[c.target];
                let mx = (n.x + other.x) / 2.0;
                let my = (n.y + other.y) / 2.0;
                let drift = (n.x - other.x) * 0.12 + sn(t * 0.3 + n.id as f64 * 0.1, 0.0, 0.5) * 12.0;
                ctx.begin_path();
                ctx.move_to(n.x, n.y);
                ctx.quadratic_curve_to(mx, my + drift, other.x, other.y);
                ctx.set_stroke_style_str(&rgba(80, 60, 50, 0.02 + c.strength * 0.06));
                ctx.set_line_width(0.5 + c.strength * 0.8);
                ctx.stroke();
            }
        }

        // ── Draw beads (the neon flow) ──
        for b in &self.beads {
            let (px, py) = bezier_pos(&self.nodes[b.from], &self.nodes[b.to], b.pos);

            // Trailing glow
            for (ti, tr) in b.trail.iter().enumerate() {
                if tr.alpha < 0.01 {
                    continue;
                }
                let tr_size = 1.0 + (ti as f64 / b.trail.len() as f64) * 4.0 * b.intensity;
                ctx.begin_path();
                let _ = ctx.arc(tr.x, tr.y, tr_size, 0.0, std::f64::consts::PI * 2.0);
                ctx.set_fill_style_str(&rgba(0, 255, 140, tr.alpha * b.intensity * 0.3));
                ctx.fill();
            }

            // The bead itself
            let bead_r = 2.5 + b.intensity * 2.5;

            // Glow halo
            ctx.begin_path();
            let _ = ctx.arc(px, py, bead_r * 3.0, 0.0, std::f64::consts::PI * 2.0);
            ctx.set_fill_style_str(&rgba(0, 255, 140, 0.08 * b.intensity));
            ctx.fill();

            // Core
            ctx.begin_path();
            let _ = ctx.arc(px, py, bead_r, 0.0, std::f64::consts::PI * 2.0);
            ctx.set_fill_style_str(&rgba(0, 255, 140, 0.6 * b.intensity));
            ctx.set_shadow_color(&rgba(0, 255, 140, 0.4 * b.intensity));
            ctx.set_shadow_blur(15.0);
            ctx.fill();
            ctx.set_shadow_blur(0.0);

            // Active edge segment (from source to bead position)
            let from_node = &self.nodes[b.from];
            let to_node = &self.nodes[b.to];
            let mx = (from_node.x + to_node.x) / 2.0;
            let my = (from_node.y + to_node.y) / 2.0;
            let drift = (from_node.x - to_node.x) * 0.12
                + sn(t * 0.3 + b.from as f64 * 0.1, 0.0, 0.5) * 12.0;
            ctx.begin_path();
            ctx.move_to(from_node.x, from_node.y);
            ctx.quadratic_curve_to(mx, my + drift, px, py);
            ctx.set_stroke_style_str(&rgba(0, 255, 140, 0.3 * b.intensity));
            ctx.set_line_width(1.0 + b.intensity * 1.5);
            ctx.set_shadow_color(&rgba(0, 255, 140, 0.2 * b.intensity));
            ctx.set_shadow_blur(8.0);
            ctx.stroke();
            ctx.set_shadow_blur(0.0);
        }

        // ── Draw nodes (junctions, subtle) ──
        for n in &self.nodes {
            let glow = n.glow;
            let r = n.r * (0.8 + glow * 0.3);

            // Subtle outer glow
            ctx.begin_path();
            let _ = ctx.arc(n.x, n.y, r * (1.0 + glow * 2.0), 0.0, std::f64::consts::PI * 2.0);
            if glow > 0.1 {
                ctx.set_fill_style_str(&rgba(0, 255, 140, 0.02 + glow * 0.06));
            } else {
                ctx.set_fill_style_str(&rgba(80, 60, 50, 0.02));
            }
            ctx.fill();

            // Core
            ctx.begin_path();
            let _ = ctx.arc(n.x, n.y, r * 0.5, 0.0, std::f64::consts::PI * 2.0);
            if glow > 0.1 {
                ctx.set_fill_style_str(&rgba(0, 255, 140, 0.1 + glow * 0.3));
            } else {
                ctx.set_fill_style_str(&rgba(180, 160, 140, 0.03 + n.act * 0.04));
            }
            ctx.fill();

            // Label
            let show_label = glow > 0.1 || (self.frame_count % 300 < 150);
            if show_label {
                let la = if glow > 0.1 {
                    (0.05 + glow * 0.4).min(0.6)
                } else {
                    0.03 + n.act * 0.04
                };
                if glow > 0.1 {
                    ctx.set_fill_style_str(&rgba(180, 255, 210, la));
                } else {
                    ctx.set_fill_style_str(&rgba(180, 160, 140, la));
                }
                ctx.set_font("7px Inter, system-ui, sans-serif");
                ctx.set_text_align("center");
                let _ = ctx.fill_text(n.name, n.x, n.y - r - 4.0);
            }
        }
    }
}

fn build_connections(nodes: &mut Vec<Node>) {
    for n in &mut *nodes {
        n.connections.clear();
    }

    let len = nodes.len();
    for i in 0..len {
        for j in (i + 1)..len {
            let (a_ptr, b_ptr) = nodes.as_mut_slice().split_at_mut(j);
            let a = &a_ptr[i];
            let b = &b_ptr[0];

            let nb = if a.name == b.name { 0.6 } else { 0.0 };
            let dx = a.x - b.x;
            let dy = a.y - b.y;
            let d = (dx * dx + dy * dy).sqrt();
            let s = (0.3 * (1.0 - d / 280.0) + nb).max(0.0);

            if s > 0.04 {
                let a = &mut a_ptr[i];
                let b = &mut b_ptr[0];
                a.connections.push(Connection {
                    target: j,
                    strength: s,
                });
                b.connections.push(Connection {
                    target: i,
                    strength: s,
                });
            }
        }
    }

    // Ensure every node has at least one connection
    for i in 0..len {
        if nodes[i].connections.is_empty() {
            let mut best = 0;
            let mut best_d = f64::MAX;
            for j in 0..len {
                if i != j {
                    let dx = nodes[i].x - nodes[j].x;
                    let dy = nodes[i].y - nodes[j].y;
                    let d = (dx * dx + dy * dy).sqrt();
                    if d < best_d {
                        best_d = d;
                        best = j;
                    }
                }
            }
            let target = best;
            let strength = 0.12;
            let (first, last) = if i < target { (i, target) } else { (target, i) };
            let (left, right) = nodes.as_mut_slice().split_at_mut(last);
            left[first].connections.push(Connection {
                target,
                strength,
            });
            right[0].connections.push(Connection {
                target: first,
                strength,
            });
        }
    }
}