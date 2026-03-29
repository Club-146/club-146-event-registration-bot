# PRD: Web Dashboard with Infographics

## Problem
Event statistics and history are only accessible via bot commands — text-only, no visual appeal. Organizers and alumni want a shareable, visual overview of meetup history.

## Solution
A public web dashboard hosted alongside the bot, reading from the same MongoDB.

## Core Views

### Timeline
- Chronological event cards: city, date, participant count
- Expandable details: venue, photos (if added later)

### Map View
- Interactive map with pins for each city (SPB, Moscow, Perm, Belgrade)
- Pin size or color intensity = participant count
- Click pin → event details popup

### Event Stats
- Total participants across all events
- Growth chart: participants per event over time
- City breakdown: bar/pie chart
- Graduation year distribution heatmap
- Payment stats: paid vs pending vs declined

### Per-Event Detail Page
- Participant count, payment summary
- Year-of-graduation distribution for that event
- Guest count

## Tech Stack
- Next.js (App Router, server components)
- shadcn/ui components
- Leaflet or Mapbox for map
- Recharts for charts
- Same MongoDB (read-only access)
- Separate container in docker-compose, port 3000

## Data Sources
- `events` collection: city, date, venue, status
- `registered_users` collection: counts, year distribution, payment status
- No PII exposed on public dashboard — only aggregated stats

## Milestones
1. Scaffold Next.js app + docker-compose service
2. Timeline + basic stats page
3. Map view with city pins
4. Per-event detail pages
5. Polish: responsive, dark mode, shareable links
