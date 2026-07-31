import { BrowserRouter, Route, Routes } from "react-router-dom";
import { MeetingDetail } from "./routes/MeetingDetail";
import { MeetingList } from "./routes/MeetingList";
import "./styles.css";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<MeetingList />} />
        <Route path="/meetings/:id" element={<MeetingDetail />} />
      </Routes>
    </BrowserRouter>
  );
}
