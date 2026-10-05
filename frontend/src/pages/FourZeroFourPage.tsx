import { Link } from "react-router-dom";
import { usePageMeta } from "../lib/pageMeta";

export function FourZeroFourPage() {
    usePageMeta({ title: 'Page not found' })
    return <div style={{ display: 'flex', flexDirection: 'column', gap: '2.5rem' }}>
        <div>
            <h2>404!</h2>
            <p>Lost in the trees? This path was reclaimed long ago...</p>
            <Link to="/" style={{ color: 'var(--primary)' }}>Follow the trail back home.</Link>
        </div>
    </div>
}